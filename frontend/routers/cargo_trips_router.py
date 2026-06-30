"""Cargo trips router — lịch sử chở hàng theo xe."""
import base64
import json
import math
import httpx
import logging
import secrets
import time
import urllib.parse
from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, Query, File, UploadFile
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu
from backend.core.handwriting_ocr import extract_trip_candidates_from_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

PER_PAGE = 20
SCAN_REVIEW_TTL = 30 * 60
SCAN_REVIEW_DIR = Path(__file__).parent.parent / ".runtime" / "cargo_scan_reviews"
OCR_SAMPLE_DIR = Path(__file__).parent.parent / ".runtime" / "cargo_ocr_samples"
_SCAN_REVIEWS: dict[str, dict] = {}


def _scan_review_path(token: str) -> Path:
    safe_token = "".join(ch for ch in token if ch.isalnum() or ch in ("-", "_"))
    return SCAN_REVIEW_DIR / f"{safe_token}.json"


def _save_scan_review(token: str, data: dict):
    _SCAN_REVIEWS[token] = data
    SCAN_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    _scan_review_path(token).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _load_scan_review(token: str) -> dict | None:
    data = _SCAN_REVIEWS.get(token)
    if data:
        return data
    path = _scan_review_path(token)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        path.unlink(missing_ok=True)
        return None
    if time.time() - data.get("created_at", 0) > SCAN_REVIEW_TTL:
        path.unlink(missing_ok=True)
        return None
    _SCAN_REVIEWS[token] = data
    return data


def _delete_scan_review(token: str):
    _SCAN_REVIEWS.pop(token, None)
    _scan_review_path(token).unlink(missing_ok=True)


def _data_uri_to_png(data_uri: str) -> bytes | None:
    if not data_uri or "," not in data_uri:
        return None
    header, payload = data_uri.split(",", 1)
    if "base64" not in header:
        return None
    try:
        return base64.b64decode(payload)
    except Exception:
        return None


def _persist_ocr_sample(
    token: str,
    vehicle_id: int,
    row_index: str,
    candidate: dict,
    corrected: dict,
    corrected_line_texts: dict[str, str] | None = None,
):
    sample_id = f"{int(time.time())}_{token[:8]}_{row_index}"
    sample_dir = OCR_SAMPLE_DIR / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)

    source_lines = []
    for line_pos, line in enumerate(candidate.get("source_lines", []), start=1):
        line_copy = dict(line)
        line_key = str(line_pos - 1)
        reviewed_text = (corrected_line_texts or {}).get(line_key)
        if reviewed_text is not None:
            line_copy["reviewed_text"] = reviewed_text.strip()
            line_copy["reviewed_text_changed"] = reviewed_text.strip() != str(line.get("text") or "").strip()
        crop_data = line_copy.pop("crop_image", "")
        crop_bytes = _data_uri_to_png(crop_data)
        if crop_bytes:
            crop_name = f"line_{line_pos:02d}.png"
            (sample_dir / crop_name).write_bytes(crop_bytes)
            line_copy["crop_file"] = crop_name
        source_lines.append(line_copy)

    ocr_public = {
        "departure_time": candidate.get("departure_time") or "",
        "return_time": candidate.get("return_time") or "",
        "origin": candidate.get("origin") or "",
        "destination": candidate.get("destination") or "",
        "fuel_liters": "" if candidate.get("fuel_liters") is None else str(candidate.get("fuel_liters")),
        "freight_revenue": "" if candidate.get("freight_revenue") is None else str(candidate.get("freight_revenue")),
        "distance_km": "" if candidate.get("distance_km") is None else str(candidate.get("distance_km")),
    }
    changed_fields = [
        field for field, value in corrected.items()
        if str(value or "").strip() != str(ocr_public.get(field) or "").strip()
    ]

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "vehicle_id": vehicle_id,
        "row_index": row_index,
        "changed_fields": changed_fields,
        "ocr": {
            "parsed": ocr_public,
            "candidate": {k: v for k, v in candidate.items() if k not in ("source_lines",)},
            "source_lines": source_lines,
        },
        "corrected": corrected,
    }
    (sample_dir / "sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _cleanup_scan_reviews():
    now = time.time()
    stale = [token for token, data in _SCAN_REVIEWS.items() if now - data.get("created_at", 0) > SCAN_REVIEW_TTL]
    for token in stale:
        _delete_scan_review(token)
    if SCAN_REVIEW_DIR.exists():
        for path in SCAN_REVIEW_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                path.unlink(missing_ok=True)
                continue
            if now - data.get("created_at", 0) > SCAN_REVIEW_TTL:
                path.unlink(missing_ok=True)


def _fmt_currency(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}".replace(",", ".") + " ₫"
    except Exception:
        return str(value)


def _num(v):
    try:
        return float(v.replace(",", ".")) if v and str(v).strip() else None
    except Exception:
        return None


def _dt(v):
    if not v or not str(v).strip():
        return None
    try:
        return datetime.fromisoformat(v)
    except Exception:
        return None


def _geocode_query_variants(q: str) -> list[str]:
    q = (q or "").strip()
    if not q:
        return []
    parts = [part.strip() for part in q.split(",") if part.strip()]
    variants = [q]
    if "việt nam" not in q.lower() and "viet nam" not in q.lower():
        variants.append(f"{q}, Việt Nam")
    if len(parts) >= 3:
        city_parts = [p for p in parts if p.lower() in {"hà nội", "ha noi", "việt nam", "viet nam"}]
        if city_parts:
            for start in (1, 0):
                if start < len(parts):
                    variants.append(", ".join([parts[start], *city_parts]))
        for start in range(1, len(parts) - 1):
            variants.append(", ".join(parts[start:]))
    seen = set()
    result = []
    for item in variants:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


async def _geocode_location(q: str) -> dict | None:
    if not q or not q.strip():
        return None
    async with httpx.AsyncClient(timeout=6) as client:
        for query in _geocode_query_variants(q):
            resp = await client.get(
                f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query)}&format=json&limit=1&accept-language=vi&countrycodes=vn",
                headers={"User-Agent": "CargoCam/1.0"}
            )
            data = resp.json()
            if data:
                return {
                    "lat": data[0]["lat"],
                    "lon": data[0]["lon"],
                    "label": data[0].get("display_name") or query,
                    "query": query,
                }
    return None


async def _resolve_location(value: str) -> dict | None:
    value = (value or "").strip()
    if not value:
        return None
    if "google.com" not in value and "goo.gl" not in value and "maps.app" not in value:
        return await _geocode_location(value)

    try:
        url = value
        if "goo.gl" in url or "maps.app.goo.gl" in url or "maps.app" in url:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.head(url, follow_redirects=True)
                url = str(resp.url)

        import re
        match = re.search(r'/place/([^/]+)', url)
        if match:
            raw = match.group(1).split("@")[0].split("?")[0]
            name = urllib.parse.unquote_plus(raw).strip().strip(",").strip()
            if name:
                geocoded = await _geocode_location(name)
                if geocoded:
                    geocoded["label"] = name
                    return geocoded
                return {"label": name}

        coord = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if not coord:
            coord = re.search(r'[?&](?:q|ll)=(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if coord:
            lat, lon = coord.group(1), coord.group(2)
            return {"lat": lat, "lon": lon, "label": value}
    except Exception:
        logger.exception("Failed to resolve location: %s", value)
    return None


async def _driving_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
    url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        data = resp.json()
    if data.get("code") != "Ok":
        return None
    dist_m = data["routes"][0]["distance"]
    return round(dist_m / 1000, 1)


@router.get("/{vehicle_id}/cargo")
async def cargo_trips_page(
    vehicle_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu("vehicles")),
):
    db = get_db()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"

    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    total = db.count_cargo_trips(vehicle_id)
    total_pages = max(1, math.ceil(total / PER_PAGE))
    page = min(page, total_pages)
    offset = (page - 1) * PER_PAGE

    trips = db.get_cargo_trips(vehicle_id, limit=PER_PAGE, offset=offset)
    for t in trips:
        t["fuel_cost_total_fmt"] = _fmt_currency(t.get("fuel_cost_total"))
        t["freight_revenue_fmt"] = _fmt_currency(t.get("freight_revenue"))
        if t.get("fuel_price_per_liter"):
            t["fuel_price_fmt"] = _fmt_currency(t["fuel_price_per_liter"])
        else:
            t["fuel_price_fmt"] = "—"
        # Convert datetime → ISO string để tojson trong template không lỗi
        for k, v in t.items():
            if k in ("departure_time", "return_time", "created_at", "updated_at") and v and hasattr(v, "isoformat"):
                t[k] = v.isoformat()

    ctx["vehicle"] = vehicle
    ctx["trips"] = trips
    ctx["page"] = page
    ctx["total_pages"] = total_pages
    ctx["total"] = total
    ctx["page_base_url"] = f"/admin/vehicles/{vehicle_id}/cargo?page="
    ctx["fmt_currency"] = _fmt_currency
    ctx["now_iso"] = datetime.now().strftime("%Y-%m-%dT%H:%M")

    return templates.TemplateResponse("pages/cargo_trips.html", {"request": request, **ctx})


@router.get("/api/resolve-maps")
async def resolve_maps_url(url: str = Query(...)):
    """Parse Google Maps URL → trả về tên địa điểm."""
    import re, urllib.parse
    try:
        if "goo.gl" in url or "maps.app.goo.gl" in url or "maps.app" in url:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.head(url, follow_redirects=True)
                url = str(resp.url)

        # Pattern 1: /place/TÊN/ — cắt trước dấu @ hoặc /data
        m = re.search(r'/place/([^/]+)', url)
        if m:
            raw = m.group(1).split("@")[0].split("?")[0]
            name = urllib.parse.unquote_plus(raw).strip().strip(",").strip()
            if name:
                # Try to geocode the name to get coords
                try:
                    async with httpx.AsyncClient(timeout=6) as client:
                        resp = await client.get(
                            f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(name)}&format=json&limit=1",
                            headers={"User-Agent": "CargoCam/1.0"}
                        )
                        gdata = resp.json()
                        if gdata:
                            return JSONResponse({"ok": True, "label": name, "lat": gdata[0]["lat"], "lon": gdata[0]["lon"]})
                except:
                    pass
                return JSONResponse({"ok": True, "label": name})

        # Pattern 2: tọa độ @lat,lon
        coord = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if not coord:
            coord = re.search(r'[?&](?:q|ll)=(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if coord:
            lat, lon = coord.group(1), coord.group(2)
            async with httpx.AsyncClient(timeout=6) as client:
                resp = await client.get(
                    f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&accept-language=vi",
                    headers={"User-Agent": "CargoCam/1.0"}
                )
                data = resp.json()
            a = data.get("address", {})
            parts = [
                a.get("road") or a.get("pedestrian"),
                a.get("suburb") or a.get("quarter") or a.get("neighbourhood"),
                a.get("city") or a.get("town") or a.get("village") or a.get("county"),
            ]
            label = ", ".join(p for p in parts if p)
            if label:
                return JSONResponse({"ok": True, "label": label, "lat": lat, "lon": lon})

        return JSONResponse({"ok": False, "msg": "Không đọc được link Google Maps."})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@router.get("/api/geocode")
async def geocode_address(q: str = Query(...)):
    """Search địa chỉ → trả về lat/lon."""
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get(
                f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(q)}&format=json&limit=1&accept-language=vi",
                headers={"User-Agent": "CargoCam/1.0"}
            )
            data = resp.json()
        if data:
            return JSONResponse({
                "ok": True,
                "lat": data[0]["lat"],
                "lon": data[0]["lon"],
                "label": data[0]["display_name"]
            })
        return JSONResponse({"ok": False, "msg": "Không tìm thấy địa điểm."})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@router.get("/api/calculate-distance")
async def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float):
    """Tính quãng đường lái xe qua OSRM (trả về km)."""
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            data = resp.json()
        if data.get("code") == "Ok":
            dist_m = data["routes"][0]["distance"]
            return JSONResponse({"ok": True, "distance_km": round(dist_m / 1000, 1)})
        return JSONResponse({"ok": False, "msg": "Không tìm được đường đi."})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@router.get("/api/calculate-distance-by-address")
async def calculate_distance_by_address(origin: str = Query(...), destination: str = Query(...)):
    """Geocode origin/destination and return driving distance without page reload."""
    try:
        origin_location = await _resolve_location(origin)
        destination_location = await _resolve_location(destination)
        if not origin_location or not destination_location:
            return JSONResponse({"ok": False, "msg": "Khong tim thay du 2 dia diem."})
        if not all(origin_location.get(k) for k in ("lat", "lon")) or not all(destination_location.get(k) for k in ("lat", "lon")):
            return JSONResponse({"ok": False, "msg": "Khong lay duoc toa do cua 2 dia diem."})

        distance_km = await _driving_distance_km(
            float(origin_location["lat"]),
            float(origin_location["lon"]),
            float(destination_location["lat"]),
            float(destination_location["lon"]),
        )
        if distance_km is None:
            return JSONResponse({"ok": False, "msg": "Khong tim duoc duong di."})

        return JSONResponse({
            "ok": True,
            "distance_km": distance_km,
            "origin": origin_location,
            "destination": destination_location,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@router.get("/api/fuel-price")
async def get_fuel_price_api(target_date: str = Query(None)):
    """Lấy giá dầu diesel từ giaxanghomnay.com theo ngày."""
    try:
        d = target_date or date.today().isoformat()
        url = f"https://giaxanghomnay.com/api/pvdate/{d}"
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        # Lấy danh sách item từ response (có thể là list hoặc list of lists)
        items = data[0] if data and isinstance(data[0], list) else data
        # Ưu tiên DO 0,05S-II (dầu diesel phổ thông)
        diesel = next(
            (r for r in items if "DO" in r.get("title", "") and "0,05" in r.get("title", "")),
            next((r for r in items if "DO" in r.get("title", "")), None)
        )
        if diesel:
            return JSONResponse({"ok": True, "price": diesel["zone1_price"], "title": diesel["title"], "date": d})
        return JSONResponse({"ok": False, "price": None})
    except Exception as e:
        return JSONResponse({"ok": False, "price": None, "error": str(e)})


@router.get("/{vehicle_id}/cargo/add")
async def add_cargo_trip_page(
    vehicle_id: int,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    db = get_db()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles", status_code=303)
    
    ctx["vehicle"] = vehicle
    # Provide a default now ISO for the form
    now = datetime.now()
    ctx["now_iso"] = now.strftime("%Y-%m-%dT%H:%M")
    return templates.TemplateResponse("pages/cargo_trip_add.html", {"request": request, **ctx})


@router.post("/{vehicle_id}/cargo/add")
async def add_cargo_trip(
    vehicle_id: int,
    departure_time: str = Form(...),
    return_time: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    destination: Optional[str] = Form(None),
    fuel_liters: Optional[str] = Form(None),
    fuel_price_per_liter: Optional[str] = Form(None),
    freight_revenue: Optional[str] = Form(None),
    distance_km: Optional[str] = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    db = get_db()
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    db.add_cargo_trip(
        vehicle_id=vehicle_id,
        plate_number=vehicle["plate_number"],
        departure_time=datetime.fromisoformat(departure_time),
        return_time=_dt(return_time),
        origin=origin or None,
        destination=destination or None,
        fuel_liters=_num(fuel_liters),
        fuel_price_per_liter=_num(fuel_price_per_liter),
        freight_revenue=_num(freight_revenue),
        distance_km=_num(distance_km),
    )
    return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo", status_code=303)


@router.get("/{vehicle_id}/cargo/{trip_id}/edit")
async def edit_cargo_trip_page(
    vehicle_id: int,
    trip_id: int,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    db = get_db()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"

    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    trip = db.get_cargo_trip(trip_id)
    if not vehicle or not trip:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    # Normalize dates for HTML datetime-local input
    for dt_col in ("departure_time", "return_time"):
        if trip.get(dt_col) and hasattr(trip[dt_col], "isoformat"):
            trip[dt_col] = trip[dt_col].isoformat()
        elif trip.get(dt_col) and isinstance(trip[dt_col], str):
            # Already normalized or string from DB
            pass

    ctx["vehicle"] = vehicle
    ctx["trip"] = trip
    return templates.TemplateResponse("pages/cargo_trip_edit.html", {"request": request, **ctx})


@router.post("/{vehicle_id}/cargo/{trip_id}/edit")
async def edit_cargo_trip(
    vehicle_id: int,
    trip_id: int,
    departure_time: str = Form(...),
    return_time: Optional[str] = Form(None),
    origin: Optional[str] = Form(None),
    destination: Optional[str] = Form(None),
    fuel_liters: Optional[str] = Form(None),
    fuel_price_per_liter: Optional[str] = Form(None),
    freight_revenue: Optional[str] = Form(None),
    distance_km: Optional[str] = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    get_db().update_cargo_trip(
        trip_id=trip_id,
        departure_time=datetime.fromisoformat(departure_time),
        return_time=_dt(return_time),
        origin=origin or None,
        destination=destination or None,
        fuel_liters=_num(fuel_liters),
        fuel_price_per_liter=_num(fuel_price_per_liter),
        freight_revenue=_num(freight_revenue),
        distance_km=_num(distance_km),
    )
    return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo", status_code=303)


@router.post("/{vehicle_id}/cargo/{trip_id}/delete")
async def delete_cargo_trip(
    vehicle_id: int,
    trip_id: int,
    user: dict = Depends(require_menu("vehicles")),
):
    get_db().delete_cargo_trip(trip_id)
    return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo", status_code=303)


@router.post("/{vehicle_id}/cargo/scan")
async def scan_handwriting(
    vehicle_id: int,
    image: UploadFile = File(...),
    user: dict = Depends(require_menu("vehicles")),
):
    _cleanup_scan_reviews()
    db = get_db()
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    image_bytes = await image.read()

    try:
        scan_result = extract_trip_candidates_from_document(
            image_bytes,
            filename=image.filename or "",
            content_type=image.content_type or "",
        )
    except Exception as e:
        logger.error("handwriting_ocr failed: %s", e)
        return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo?scan_error=1", status_code=303)

    token = secrets.token_urlsafe(24)
    _save_scan_review(token, {
        "created_at": time.time(),
        "vehicle_id": vehicle_id,
        "user_id": user.get("id"),
        "filename": image.filename or "",
        "content_type": image.content_type or "",
        "scan_result": scan_result,
    })
    return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo/scan/review?token={token}", status_code=303)


@router.get("/{vehicle_id}/cargo/scan/review")
async def review_cargo_scan(
    vehicle_id: int,
    request: Request,
    token: str = Query(...),
    user: dict = Depends(require_menu("vehicles")),
):
    _cleanup_scan_reviews()
    db = get_db()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"

    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    review = _load_scan_review(token)
    if not vehicle or not review or review.get("vehicle_id") != vehicle_id or review.get("user_id") != user.get("id"):
        return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo?scan_error=1", status_code=303)

    scan_result = review["scan_result"]
    ctx["vehicle"] = vehicle
    ctx["token"] = token
    ctx["filename"] = review.get("filename", "")
    ctx["lines"] = scan_result.get("lines", [])
    ctx["candidates"] = scan_result.get("candidates", [])
    ctx["line_count"] = scan_result.get("line_count", 0)
    ctx["candidate_count"] = scan_result.get("candidate_count", 0)
    ctx["quality"] = scan_result.get("quality", {})
    ctx["quality_warnings"] = scan_result.get("quality_warnings", [])
    return templates.TemplateResponse("pages/cargo_scan_review.html", {"request": request, **ctx})


@router.post("/{vehicle_id}/cargo/scan/confirm")
async def confirm_cargo_scan(
    vehicle_id: int,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    _cleanup_scan_reviews()
    form = await request.form()
    token = str(form.get("token") or "")
    review = _load_scan_review(token)

    db = get_db()
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle or not review or review.get("vehicle_id") != vehicle_id or review.get("user_id") != user.get("id"):
        return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo?scan_error=1", status_code=303)

    saved = 0
    skipped = 0
    scan_result = review.get("scan_result", {})
    candidates = scan_result.get("candidates", [])
    row_indexes = sorted({
        key.split("_", 1)[1]
        for key in form.keys()
        if key.startswith("departure_") or key.startswith("skip_")
    }, key=lambda v: int(v) if v.isdigit() else 0)

    for idx in row_indexes:
        if form.get(f"skip_{idx}") == "on":
            skipped += 1
            continue
        departure_time = str(form.get(f"departure_{idx}") or "").strip()
        if not departure_time:
            skipped += 1
            continue
        corrected = {
            "departure_time": departure_time,
            "return_time": str(form.get(f"return_{idx}") or "").strip(),
            "origin": str(form.get(f"origin_{idx}") or "").strip(),
            "destination": str(form.get(f"destination_{idx}") or "").strip(),
            "fuel_liters": str(form.get(f"fuel_{idx}") or "").strip(),
            "freight_revenue": str(form.get(f"revenue_{idx}") or "").strip(),
            "distance_km": str(form.get(f"distance_{idx}") or "").strip(),
        }
        corrected_line_texts = {
            key.rsplit("_", 1)[1]: str(value or "").strip()
            for key, value in form.items()
            if key.startswith(f"line_text_{idx}_")
        }
        try:
            db.add_cargo_trip(
                vehicle_id=vehicle_id,
                plate_number=vehicle["plate_number"],
                departure_time=datetime.fromisoformat(departure_time),
                return_time=_dt(corrected["return_time"]),
                origin=(corrected["origin"] or None),
                destination=(corrected["destination"] or None),
                fuel_liters=_num(corrected["fuel_liters"]),
                fuel_price_per_liter=None,
                freight_revenue=_num(corrected["freight_revenue"]),
                distance_km=_num(corrected["distance_km"]),
            )
            if idx.isdigit() and int(idx) < len(candidates):
                try:
                    _persist_ocr_sample(token, vehicle_id, idx, candidates[int(idx)], corrected, corrected_line_texts)
                except Exception as sample_error:
                    logger.warning("Failed to persist cargo OCR sample: %s | row=%s", sample_error, idx)
            saved += 1
        except Exception as e:
            skipped += 1
            logger.warning("Failed to save reviewed cargo scan row: %s | row=%s", e, idx)

    _delete_scan_review(token)
    return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo?scanned={saved}&scan_skipped={skipped}", status_code=303)
