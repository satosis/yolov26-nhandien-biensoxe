"""Cargo trips router — lịch sử chở hàng theo xe."""
import math
import httpx
import logging
from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, Query, File, UploadFile
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu
from backend.core.handwriting_ocr import extract_trips_from_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

PER_PAGE = 20


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
        return RedirectResponse(url="/camera/vehicles", status_code=303)

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
    ctx["page_base_url"] = f"/camera/vehicles/{vehicle_id}/cargo?page="
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
        return RedirectResponse(url="/camera/vehicles", status_code=303)
    
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
        return RedirectResponse(url="/camera/vehicles", status_code=303)

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
    return RedirectResponse(url=f"/camera/vehicles/{vehicle_id}/cargo", status_code=303)


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
        return RedirectResponse(url="/camera/vehicles", status_code=303)

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
    return RedirectResponse(url=f"/camera/vehicles/{vehicle_id}/cargo", status_code=303)


@router.post("/{vehicle_id}/cargo/{trip_id}/delete")
async def delete_cargo_trip(
    vehicle_id: int,
    trip_id: int,
    user: dict = Depends(require_menu("vehicles")),
):
    get_db().delete_cargo_trip(trip_id)
    return RedirectResponse(url=f"/camera/vehicles/{vehicle_id}/cargo", status_code=303)


@router.post("/{vehicle_id}/cargo/scan")
async def scan_handwriting(
    vehicle_id: int,
    image: UploadFile = File(...),
    user: dict = Depends(require_menu("vehicles")),
):
    db = get_db()
    vehicle = db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)
    if not vehicle:
        return RedirectResponse(url="/camera/vehicles", status_code=303)

    image_bytes = await image.read()

    try:
        trips = extract_trips_from_image(image_bytes)
    except Exception as e:
        logger.error("handwriting_ocr failed: %s", e)
        return RedirectResponse(url=f"/camera/vehicles/{vehicle_id}/cargo?scan_error=1", status_code=303)

    saved = 0
    for t in trips:
        if not t.get("departure_time"):
            continue
        try:
            db.add_cargo_trip(
                vehicle_id=vehicle_id,
                plate_number=vehicle["plate_number"],
                departure_time=datetime.fromisoformat(t["departure_time"]),
                return_time=_dt(t.get("return_time")),
                origin=t.get("origin"),
                destination=t.get("destination"),
                fuel_liters=t.get("fuel_liters"),
                fuel_price_per_liter=None,
                freight_revenue=t.get("freight_revenue"),
            )
            saved += 1
        except Exception as e:
            logger.warning("Failed to save trip row: %s | row: %s", e, t)

    return RedirectResponse(url=f"/camera/vehicles/{vehicle_id}/cargo?scanned={saved}", status_code=303)
