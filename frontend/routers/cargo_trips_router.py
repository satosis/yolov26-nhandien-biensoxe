import base64
import calendar
from html import escape
import json
import math
import httpx
import logging
import re
import secrets
import time
import urllib.parse
from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, Query, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import RedirectResponse
from pathlib import Path
from frontend.deps import get_menu_context, require_menu, templates
from backend.services.database import db
from backend.ai.handwriting_ocr import extract_trip_candidates_from_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles")

PER_PAGE = 20
SCAN_REVIEW_TTL = 30 * 60
SCAN_REVIEW_DIR = Path(__file__).parent.parent / ".runtime" / "cargo_scan_reviews"
OCR_SAMPLE_DIR = Path(__file__).parent.parent / ".runtime" / "cargo_ocr_samples"
_SCAN_REVIEWS: dict[str, dict] = {}
_TRIP_DATE_RE = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})"
    r"(?::(?P<second>\d{2})(?:\.(?P<fraction>\d{1,6}))?)?)?",
    re.ASCII,
)
_TRIP_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?(?:[eE][+-]?\d{1,3})?", re.ASCII)
_SOURCE_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?", re.ASCII)
_TRIP_NUMBER_LABELS = {
    "fuel_liters": "Số lít dầu",
    "fuel_price_per_liter": "Giá dầu",
    "freight_revenue": "Cước vận chuyển",
    "distance_km": "Quãng đường",
}


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
    data = json.loads(path.read_text(encoding="utf-8"))
    if time.time() - data.get("created_at", 0) > SCAN_REVIEW_TTL:
        path.unlink(missing_ok=True)
        return None
    _SCAN_REVIEWS[token] = data
    return data


def _delete_scan_review(token: str):
    _SCAN_REVIEWS.pop(token, None)
    _scan_review_path(token).unlink(missing_ok=True)


def get_cargo_trips(vehicle_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    query = """
        SELECT * FROM cargo_trips
        WHERE vehicle_id = %s
        ORDER BY departure_time DESC
        LIMIT %s OFFSET %s
    """
    return db._execute_query(query, (vehicle_id, limit, offset), fetchall=True, commit=False)


def count_cargo_trips(vehicle_id: int) -> int:
    row = db._execute_query(
        "SELECT COUNT(*) FROM cargo_trips WHERE vehicle_id = %s",
        (vehicle_id,), fetch=True, commit=False
    )
    return row["count"] if row else 0


def add_cargo_trip(vehicle_id: int, plate_number: str, departure_time,
                   return_time=None, origin=None, destination=None,
                   fuel_liters=None, fuel_price_per_liter=None,
                   freight_revenue=None, distance_km=None) -> int | None:
    query = """
        INSERT INTO cargo_trips
            (vehicle_id, plate_number, departure_time, return_time,
             origin, destination,
             fuel_liters, fuel_price_per_liter,
             freight_revenue, distance_km)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """
    trip_id = db._execute_query(
        query,
        (vehicle_id, plate_number, departure_time, return_time,
         origin, destination,
         fuel_liters, fuel_price_per_liter,
         freight_revenue, distance_km),
        commit=True, return_id=True
    )
    if trip_id is None:
        logger.error("Cargo trip insert returned no ID for vehicle %s", vehicle_id)
    return trip_id


def update_cargo_trip(trip_id: int, *, departure_time, return_time, origin,
                      destination, fuel_liters, fuel_price_per_liter,
                      freight_revenue, distance_km) -> bool:
    return db._execute_query(
        """UPDATE cargo_trips SET departure_time = %s, return_time = %s,
           origin = %s, destination = %s, fuel_liters = %s,
           fuel_price_per_liter = %s, freight_revenue = %s,
           distance_km = %s, updated_at = NOW() WHERE id = %s""",
        (departure_time, return_time, origin, destination, fuel_liters,
         fuel_price_per_liter, freight_revenue, distance_km, trip_id),
        commit=True,
    )


def get_cargo_trip(trip_id: int) -> dict | None:
    return db._execute_query("SELECT * FROM cargo_trips WHERE id = %s", (trip_id,), fetch=True, commit=False)


def delete_cargo_trip(trip_id: int) -> bool:
    return db.delete("cargo_trips", {"id": trip_id})


def add_reviewed_cargo_trips(vehicle_id: int, plate_number: str, trips: list[dict]) -> bool:
    if not trips:
        return True
    columns = (
        "vehicle_id", "plate_number", "departure_time", "return_time", "origin",
        "destination", "fuel_liters", "fuel_price_per_liter", "freight_revenue", "distance_km",
    )
    placeholders = ", ".join(["(" + ", ".join(["%s"] * len(columns)) + ")"] * len(trips))
    values = tuple(
        value
        for trip in trips
        for value in (vehicle_id, plate_number, *(trip[column] for column in columns[2:]))
    )
    query = "INSERT INTO cargo_trips (" + ", ".join(columns) + ") VALUES " + placeholders + " RETURNING id"
    rows = db._execute_query(query, values, fetchall=True, commit=True)
    if not isinstance(rows, list) or len(rows) != len(trips):
        logger.error(
            "Cargo trip bulk insert returned %s IDs for %s rows on vehicle %s",
            len(rows) if isinstance(rows, list) else 0, len(trips), vehicle_id,
        )
        return False
    return True


def _data_uri_to_png(data_uri: str) -> bytes | None:
    if not data_uri or "," not in data_uri:
        return None
    header, payload = data_uri.split(",", 1)
    if "base64" not in header:
        return None
    return base64.b64decode(payload)


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
            data = json.loads(path.read_text(encoding="utf-8"))
            if now - data.get("created_at", 0) > SCAN_REVIEW_TTL:
                path.unlink(missing_ok=True)


def _fmt_currency(value) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}".replace(",", ".") + " ₫"


def _num(v):
    return float(v.replace(",", ".")) if v and v.strip() else None


def _dt(v):
    return datetime.fromisoformat(v) if v and v.strip() else None


def _valid_trip_datetime(value: str) -> bool:
    match = _TRIP_DATE_RE.fullmatch(value)
    if match is None:
        return False
    year, month, day = (int(match[name]) for name in ("year", "month", "day"))
    if not (1 <= year <= 9999 and 1 <= month <= 12):
        return False
    if not (1 <= day <= calendar.monthrange(year, month)[1]):
        return False
    if match["hour"] is not None:
        return (
            0 <= int(match["hour"]) <= 23
            and 0 <= int(match["minute"]) <= 59
            and (match["second"] is None or 0 <= int(match["second"]) <= 59)
        )
    return True


def _normalise_trip_fields(raw: dict[str, str | None]) -> tuple[dict | None, str | None]:
    values = {field: str(raw.get(field) or "").strip() for field in (
        "departure_time", "return_time", "origin", "destination", *_TRIP_NUMBER_LABELS,
    )}
    if not values["departure_time"] or not _valid_trip_datetime(values["departure_time"]):
        return None, "Ngày xuất phát không hợp lệ."
    if values["return_time"] and not _valid_trip_datetime(values["return_time"]):
        return None, "Ngày về không hợp lệ."
    numbers = {}
    for field, label in _TRIP_NUMBER_LABELS.items():
        value = values[field]
        if not value:
            numbers[field] = None
            continue
        if len(value) > 40 or _TRIP_NUMBER_RE.fullmatch(value) is None:
            return None, f"{label} phải là số không âm hợp lệ."
        number = _num(value)
        if not math.isfinite(number) or number > 1_000_000_000_000:
            return None, f"{label} phải là số không âm hợp lệ."
        numbers[field] = number

    departure = datetime.fromisoformat(values["departure_time"])
    returned = _dt(values["return_time"])
    if returned is not None and returned < departure:
        return None, "Ngày về phải từ ngày xuất phát trở đi."
    return {
        "departure_time": departure,
        "return_time": returned,
        "origin": values["origin"] or None,
        "destination": values["destination"] or None,
        **numbers,
    }, None


def _trip_form_error(message: str, back_url: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='vi'><meta charset='utf-8'>"
        "<title>Dữ liệu chuyến hàng không hợp lệ</title>"
        "<body style='font:16px system-ui;max-width:40rem;margin:3rem auto;padding:1rem'>"
        "<h1>Chưa lưu chuyến hàng</h1>"
        f"<p>{escape(message)}</p>"
        "<button type='button' onclick='history.back()'>Quay lại sửa</button> "
        f"<a href='{escape(back_url, quote=True)}'>Mở lại biểu mẫu</a>"
        "</body></html>",
        status_code=422,
    )


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


def _source_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value).strip()
    if len(text) > 40 or _SOURCE_NUMBER_RE.fullmatch(text) is None:
        return None
    number = float(text)
    return number if math.isfinite(number) else None


def _coordinates(location: dict) -> tuple[float, float] | None:
    lat = _source_number(location.get("lat"))
    lon = _source_number(location.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _geocode_result(data: object, fallback_label: str) -> dict | None:
    if not isinstance(data, list):
        logger.warning("Nominatim response is not a list")
        return None
    if not data:
        return None
    if not isinstance(data[0], dict):
        logger.warning("Nominatim response contains an invalid location")
        return None
    coordinates = _coordinates(data[0])
    if coordinates is None:
        logger.warning("Nominatim response contains invalid coordinates")
        return None
    label = data[0].get("display_name")
    return {
        "lat": coordinates[0],
        "lon": coordinates[1],
        "label": label.strip() if isinstance(label, str) and label.strip() else fallback_label,
    }


async def _geocode_location(q: str) -> dict | None:
    if not q or not q.strip():
        return None
    async with httpx.AsyncClient(timeout=6) as client:
        for query in _geocode_query_variants(q):
            resp = await client.get(
                f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query)}&format=json&limit=1&accept-language=vi&countrycodes=vn",
                headers={"User-Agent": "CargoCam/1.0"}
            )
            if resp.is_error:
                logger.warning("Nominatim location lookup returned HTTP %s", resp.status_code)
            resp.raise_for_status()
            data = resp.json()
            location = _geocode_result(data, query)
            if location:
                return {**location, "query": query}
    return None


_SHORT_MAPS_HOSTS = {"goo.gl", "maps.app.goo.gl", "maps.app"}
_MAPS_URL_RE = re.compile(
    r"https://(?:google\.com|www\.google\.com|maps\.google\.com|goo\.gl|maps\.app\.goo\.gl|maps\.app)(?:[/?#][^\x00-\x20\x7f]*)?",
    re.IGNORECASE,
)


def _is_maps_url(value: str) -> bool:
    return _MAPS_URL_RE.fullmatch(value) is not None


async def _expand_maps_url(url: str) -> str | None:
    if not _is_maps_url(url):
        return None
    if urllib.parse.urlparse(url).hostname not in _SHORT_MAPS_HOSTS:
        return url
    async with httpx.AsyncClient(timeout=8) as client:
        for _ in range(4):
            response = await client.head(url, follow_redirects=False)
            if response.is_error:
                logger.warning("Google Maps short link request returned HTTP %s", response.status_code)
                response.raise_for_status()
            location = response.headers.get("location")
            if response.status_code not in (301, 302, 303, 307, 308) or not location:
                logger.warning("Google Maps short link did not provide a redirect")
                return None
            if location.startswith("//"):
                url = "https:" + location
            elif location.startswith(("http://", "https://")):
                url = location
            else:
                url = urllib.parse.urljoin(url, location)
            if not _is_maps_url(url):
                logger.warning("Google Maps short link redirected to an unsupported host")
                return None
            if urllib.parse.urlparse(url).hostname not in _SHORT_MAPS_HOSTS:
                return url
    logger.warning("Google Maps short link exceeded the redirect limit")
    return None


async def _resolve_location(value: str) -> dict | None:
    value = (value or "").strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        return await _geocode_location(value)
    url = await _expand_maps_url(value)
    if url is None:
        return None

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
            return None

    coord = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if not coord:
        coord = re.search(r'[?&](?:q|ll)=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if coord:
        lat, lon = coord.group(1), coord.group(2)
        coordinates = _coordinates({"lat": lat, "lon": lon})
        if coordinates:
            return {"lat": coordinates[0], "lon": coordinates[1], "label": value}
    return None


async def _driving_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
    url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        if resp.is_error:
            logger.warning("OSRM route lookup returned HTTP %s", resp.status_code)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict) or data.get("code") != "Ok":
        logger.warning("OSRM route response did not report success")
        return None
    routes = data.get("routes")
    if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
        logger.warning("OSRM route response is missing a route")
        return None
    dist_m = _source_number(routes[0].get("distance"))
    if dist_m is None or dist_m < 0:
        logger.warning("OSRM route response has an invalid distance")
        return None
    return round(dist_m / 1000, 1)


@router.get("/{vehicle_id}/cargo")
async def cargo_trips_page(
    vehicle_id: int,
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"

    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    total = count_cargo_trips(vehicle_id)
    total_pages = max(1, math.ceil(total / PER_PAGE))
    page = min(page, total_pages)
    offset = (page - 1) * PER_PAGE

    trips = get_cargo_trips(vehicle_id, limit=PER_PAGE, offset=offset)
    for t in trips:
        t["fuel_cost_total_fmt"] = _fmt_currency(t.get("fuel_cost_total"))
        t["freight_revenue_fmt"] = _fmt_currency(t.get("freight_revenue"))
        if t.get("fuel_price_per_liter"):
            t["fuel_price_fmt"] = _fmt_currency(t["fuel_price_per_liter"])
        else:
            t["fuel_price_fmt"] = "—"
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
    if not _is_maps_url(url):
        return JSONResponse({"ok": False, "message": "Link Google Maps không hợp lệ."}, status_code=422)
    location = await _resolve_location(url)
    if location is None:
        return JSONResponse({"ok": False, "message": "Không lấy được tọa độ từ link Google Maps."}, status_code=404)
    if location["label"] == url:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get(
                f"https://nominatim.openstreetmap.org/reverse?lat={location['lat']}&lon={location['lon']}&format=json&accept-language=vi",
                headers={"User-Agent": "CargoCam/1.0"}
            )
            if not resp.is_success:
                logger.warning("Nominatim reverse geocoding returned HTTP %s", resp.status_code)
                return JSONResponse({"ok": False, "message": "Dịch vụ bản đồ không khả dụng."}, status_code=502)
            data = resp.json()
        a = data.get("address", {}) if isinstance(data, dict) else {}
        if not isinstance(a, dict):
            a = {}
        parts = [
            a.get("road") or a.get("pedestrian"),
            a.get("suburb") or a.get("quarter") or a.get("neighbourhood"),
            a.get("city") or a.get("town") or a.get("village") or a.get("county"),
        ]
        label = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        if label:
            location["label"] = label
    return JSONResponse({"ok": True, **location})


@router.get("/api/geocode")
async def geocode_address(q: str = Query(...)):
    async with httpx.AsyncClient(timeout=6) as client:
        resp = await client.get(
            f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(q)}&format=json&limit=1&accept-language=vi",
            headers={"User-Agent": "CargoCam/1.0"}
        )
        if not resp.is_success:
            logger.warning("Nominatim geocoding returned HTTP %s", resp.status_code)
            return JSONResponse({"ok": False, "message": "Dịch vụ bản đồ không khả dụng."}, status_code=502)
        data = resp.json()
    location = _geocode_result(data, q)
    if location:
        return JSONResponse({"ok": True, **location})
    return JSONResponse({"ok": False, "message": "Không tìm thấy địa điểm."}, status_code=404)


@router.get("/api/calculate-distance")
async def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float):
    if _coordinates({"lat": lat1, "lon": lon1}) is None or _coordinates({"lat": lat2, "lon": lon2}) is None:
        return JSONResponse({"ok": False, "message": "Tọa độ không hợp lệ."}, status_code=422)
    distance = await _driving_distance_km(lat1, lon1, lat2, lon2)
    if distance is not None:
        return JSONResponse({"ok": True, "distance_km": distance})
    return JSONResponse({"ok": False, "message": "Không tìm được đường đi."}, status_code=404)


@router.get("/api/calculate-distance-by-address")
async def calculate_distance_by_address(origin: str = Query(...), destination: str = Query(...)):
    origin_location = await _resolve_location(origin)
    destination_location = await _resolve_location(destination)
    if origin_location is None or destination_location is None:
        return JSONResponse({"ok": False, "message": "Không tìm thấy đủ hai địa điểm."}, status_code=404)

    distance_km = await _driving_distance_km(
        origin_location["lat"], origin_location["lon"],
        destination_location["lat"], destination_location["lon"],
    )
    if distance_km is None:
        return JSONResponse({"ok": False, "message": "Không tìm được đường đi."}, status_code=404)

    return JSONResponse({
        "ok": True,
        "distance_km": distance_km,
        "origin": origin_location,
        "destination": destination_location,
    })


@router.get("/api/fuel-price")
async def get_fuel_price_api(target_date: str = Query(None)):
    d = target_date or date.today().isoformat()
    if len(d) != 10 or not _valid_trip_datetime(d):
        return JSONResponse({"ok": False, "message": "Ngày tra giá dầu không hợp lệ.", "price": None}, status_code=422)
    url = f"https://giaxanghomnay.com/api/pvdate/{d}"
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(url)
        if resp.is_error:
            logger.warning("Fuel price request returned HTTP %s", resp.status_code)
        resp.raise_for_status()
        data = resp.json()
    items = data[0] if isinstance(data, list) and data and isinstance(data[0], list) else data
    if not isinstance(items, list):
        logger.warning("Fuel price response is not a list")
        return JSONResponse({"ok": False, "message": "Dữ liệu giá dầu không hợp lệ.", "price": None}, status_code=502)
    diesel_items = [r for r in items if isinstance(r, dict) and isinstance(r.get("title"), str)]
    if items and not diesel_items:
        logger.warning("Fuel price response contains no valid entries")
    diesel = next(
        (r for r in diesel_items if "DO" in r["title"] and "0,05" in r["title"]),
        next((r for r in diesel_items if "DO" in r["title"]), None)
    )
    if diesel:
        price = _source_number(diesel.get("zone1_price"))
        if price is not None and price > 0:
            return JSONResponse({"ok": True, "price": price, "title": diesel["title"], "date": d})
    return JSONResponse({"ok": False, "message": "Không tìm thấy giá dầu.", "price": None}, status_code=404)


@router.get("/{vehicle_id}/cargo/add")
async def add_cargo_trip_page(
    vehicle_id: int,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles", status_code=303)
    
    ctx["vehicle"] = vehicle
    now = datetime.now()
    ctx["now_iso"] = now.strftime("%Y-%m-%dT%H:%M")
    return templates.TemplateResponse("pages/cargo_trip_add.html", {"request": request, **ctx})


@router.post("/{vehicle_id}/cargo/add")
async def create_cargo_trip(
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
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    trip_fields, error = _normalise_trip_fields({
        "departure_time": departure_time,
        "return_time": return_time,
        "origin": origin,
        "destination": destination,
        "fuel_liters": fuel_liters,
        "fuel_price_per_liter": fuel_price_per_liter,
        "freight_revenue": freight_revenue,
        "distance_km": distance_km,
    })
    if error:
        return _trip_form_error(error, f"/admin/vehicles/{vehicle_id}/cargo/add")

    trip_id = add_cargo_trip(
        vehicle_id=vehicle_id,
        plate_number=vehicle["plate_number"],
        **trip_fields,
    )
    if trip_id is None:
        return HTMLResponse("Không thể lưu chuyến hàng.", status_code=500)
    return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo", status_code=303)


@router.get("/{vehicle_id}/cargo/{trip_id}/edit")
async def edit_cargo_trip_page(
    vehicle_id: int,
    trip_id: int,
    request: Request,
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"

    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    trip = get_cargo_trip(trip_id)
    if not vehicle or not trip or trip.get("vehicle_id") != vehicle_id:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    for dt_col in ("departure_time", "return_time"):
        if trip.get(dt_col) and hasattr(trip[dt_col], "isoformat"):
            trip[dt_col] = trip[dt_col].isoformat()

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
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    trip = get_cargo_trip(trip_id)
    if not vehicle or not trip or trip.get("vehicle_id") != vehicle_id:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    trip_fields, error = _normalise_trip_fields({
        "departure_time": departure_time,
        "return_time": return_time,
        "origin": origin,
        "destination": destination,
        "fuel_liters": fuel_liters,
        "fuel_price_per_liter": fuel_price_per_liter,
        "freight_revenue": freight_revenue,
        "distance_km": distance_km,
    })
    if error:
        return _trip_form_error(error, f"/admin/vehicles/{vehicle_id}/cargo/{trip_id}/edit")

    if not update_cargo_trip(trip_id=trip_id, **trip_fields):
        return HTMLResponse("Không tìm thấy chuyến hàng.", status_code=404)
    return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo", status_code=303)


@router.post("/{vehicle_id}/cargo/{trip_id}/delete")
async def delete_cargo_trip_endpoint(
    vehicle_id: int,
    trip_id: int,
    user: dict = Depends(require_menu("vehicles")),
):
    trip = get_cargo_trip(trip_id)
    if not trip or trip.get("vehicle_id") != vehicle_id:
        return JSONResponse({"ok": False, "message": "Không tìm thấy chuyến hàng."}, status_code=404)
    if not delete_cargo_trip(trip_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy chuyến hàng."}, status_code=404)
    return {"ok": True}


@router.post("/{vehicle_id}/cargo/scan")
async def scan_handwriting(
    vehicle_id: int,
    image: UploadFile = File(...),
    user: dict = Depends(require_menu("vehicles")),
):
    _cleanup_scan_reviews()
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/admin/vehicles", status_code=303)

    image_bytes = await image.read()

    scan_result = extract_trip_candidates_from_document(
        image_bytes,
        filename=image.filename or "",
        content_type=image.content_type or "",
    )

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

    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle or not review or review.get("vehicle_id") != vehicle_id or review.get("user_id") != user.get("id"):
        return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo?scan_error=1", status_code=303)

    skipped = 0
    scan_result = review.get("scan_result", {})
    candidates = scan_result.get("candidates", [])
    pending_rows = []
    for row_index in range(len(candidates)):
        idx = str(row_index)
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
        trip_fields, error = _normalise_trip_fields({**corrected, "fuel_price_per_liter": None})
        if error:
            return _trip_form_error(
                f"Chuyến {row_index + 1}: {error}",
                f"/admin/vehicles/{vehicle_id}/cargo/scan/review?token={urllib.parse.quote(token)}",
            )
        corrected_line_texts = {
            key.rsplit("_", 1)[1]: str(value or "").strip()
            for key, value in form.items()
            if key.startswith(f"line_text_{idx}_")
        }
        pending_rows.append((row_index, trip_fields, corrected, corrected_line_texts))

    if not add_reviewed_cargo_trips(
        vehicle_id, vehicle["plate_number"], [trip_fields for _, trip_fields, _, _ in pending_rows],
    ):
        return HTMLResponse("Không thể lưu các chuyến hàng đã duyệt.", status_code=500)

    for row_index, trip_fields, corrected, corrected_line_texts in pending_rows:
        _persist_ocr_sample(token, vehicle_id, str(row_index), candidates[row_index], corrected, corrected_line_texts)

    _delete_scan_review(token)
    return RedirectResponse(url=f"/admin/vehicles/{vehicle_id}/cargo?scanned={len(pending_rows)}&scan_skipped={skipped}", status_code=303)
