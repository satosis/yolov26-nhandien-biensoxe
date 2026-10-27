import logging
import os
from typing import Optional, Tuple, Dict, Any

import httpx

from dotenv import find_dotenv, load_dotenv

logger = logging.getLogger(__name__)


def get_google_maps_api_key() -> str:
    """Retrieve the shared Google API key from the environment."""
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY") or ""
    if not key:
        load_dotenv(find_dotenv() or None)
        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY") or ""
    return key.strip()


def get_google_maps_referer() -> str:
    """Retrieve the HTTP Referer header required for restricted Google Maps API keys."""
    return (os.getenv("GOOGLE_MAPS_REFERER") or "http://localhost:8000/").strip()


def _format_location_param(loc: Any) -> str:
    """Format location as 'lat,lon' string or raw string."""
    if isinstance(loc, dict):
        lat = loc.get("lat") or loc.get("latitude")
        lon = loc.get("lon") or loc.get("lng") or loc.get("longitude")
        if lat is not None and lon is not None:
            return f"{lat},{lon}"
    return str(loc or "").strip()


async def compute_routes_api(
    origin: Any,
    destination: Any,
    api_key: Optional[str] = None,
    referer: Optional[str] = None,
) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
    """
    Calculate driving distance using Google Routes API (New v2).
    POST https://routes.googleapis.com/directions/v2:computeRoutes
    """
    key = api_key or get_google_maps_api_key()
    if not key:
        return None, None

    def _build_waypoint(item: Any) -> dict:
        if isinstance(item, dict) and "lat" in item and ("lon" in item or "lng" in item):
            lat = float(item["lat"])
            lon = float(item.get("lon") if "lon" in item else item.get("lng"))
            return {"location": {"latLng": {"latitude": lat, "longitude": lon}}}
        s = str(item).strip()
        parts = s.split(",")
        if len(parts) == 2:
            try:
                lat, lon = float(parts[0].strip()), float(parts[1].strip())
                return {"location": {"latLng": {"latitude": lat, "longitude": lon}}}
            except ValueError:
                pass
        return {"address": s}

    body = {
        "origin": _build_waypoint(origin),
        "destination": _build_waypoint(destination),
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_UNAWARE",
    }

    url = "https://routes.googleapis.com/directions/v2:computeRoutes"

    candidate_referers = []
    if referer:
        candidate_referers.append(referer)
    default_ref = get_google_maps_referer()
    if default_ref not in candidate_referers:
        candidate_referers.append(default_ref)
    for alt in ("http://localhost:8000/", "http://127.0.0.1:8000/"):
        if alt not in candidate_referers:
            candidate_referers.append(alt)

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for ref in candidate_referers:
                headers = {
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": key,
                    "X-Goog-FieldMask": "routes.distanceMeters,routes.duration",
                    "Referer": ref,
                }
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    routes = data.get("routes")
                    if isinstance(routes, list) and routes:
                        dist_meters = routes[0].get("distanceMeters")
                        if dist_meters is not None:
                            dist_km = round(float(dist_meters) / 1000.0, 1)
                            return dist_km, {
                                "provider": "google_routes",
                                "distance_meters": dist_meters,
                                "duration": routes[0].get("duration"),
                            }
                elif resp.status_code == 403 and "API_KEY_HTTP_REFERRER_BLOCKED" in resp.text:
                    continue
                else:
                    logger.warning("Google Routes API HTTP %s: %s", resp.status_code, resp.text[:300])
                    break
    except Exception as exc:
        logger.warning("Google Routes API request failed: %s", exc)

    return None, None


async def compute_directions_api(
    origin: Any,
    destination: Any,
    api_key: Optional[str] = None,
) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
    """
    Calculate driving distance using Google Directions API.
    GET https://maps.googleapis.com/maps/api/directions/json
    """
    key = api_key or get_google_maps_api_key()
    if not key:
        return None, None

    orig_str = _format_location_param(origin)
    dest_str = _format_location_param(destination)
    if not orig_str or not dest_str:
        return None, None

    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": orig_str,
        "destination": dest_str,
        "mode": "driving",
        "language": "vi",
        "key": key,
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "OK":
                    routes = data.get("routes", [])
                    if routes and "legs" in routes[0] and routes[0]["legs"]:
                        leg = routes[0]["legs"][0]
                        dist_val = leg.get("distance", {}).get("value")
                        if dist_val is not None:
                            dist_km = round(float(dist_val) / 1000.0, 1)
                            return dist_km, {
                                "provider": "google_directions",
                                "distance_meters": dist_val,
                                "duration_text": leg.get("duration", {}).get("text"),
                                "start_address": leg.get("start_address"),
                                "end_address": leg.get("end_address"),
                            }
                else:
                    logger.warning("Google Directions API status '%s': %s",
                                   data.get("status"), data.get("error_message", ""))
            else:
                logger.warning("Google Directions API HTTP %s", resp.status_code)
    except Exception as exc:
        logger.warning("Google Directions API request failed: %s", exc)

    return None, None


async def geocode_address_google(
    address: str,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Geocode an address to latitude/longitude using Google Geocoding API.
    GET https://maps.googleapis.com/maps/api/geocode/json
    """
    key = api_key or get_google_maps_api_key()
    if not key or not address or not address.strip():
        return None

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address.strip(),
        "language": "vi",
        "region": "vn",
        "key": key,
    }

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "OK":
                    results = data.get("results", [])
                    if results:
                        top = results[0]
                        loc = top.get("geometry", {}).get("location", {})
                        lat, lon = loc.get("lat"), loc.get("lng")
                        if lat is not None and lon is not None:
                            return {
                                "lat": float(lat),
                                "lon": float(lon),
                                "label": top.get("formatted_address") or address,
                                "provider": "google_geocode",
                                "place_id": top.get("place_id"),
                            }
                else:
                    logger.warning("Google Geocoding status '%s' for '%s': %s",
                                   data.get("status"), address, data.get("error_message", ""))
    except Exception as exc:
        logger.warning("Google Geocoding request failed: %s", exc)

    return None


async def reverse_geocode_google(
    lat: float,
    lon: float,
    api_key: Optional[str] = None,
) -> Optional[str]:
    """
    Reverse geocode latitude/longitude to a readable Vietnamese address.
    GET https://maps.googleapis.com/maps/api/geocode/json?latlng=...
    """
    key = api_key or get_google_maps_api_key()
    if not key:
        return None

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "latlng": f"{lat},{lon}",
        "language": "vi",
        "key": key,
    }

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "OK":
                    results = data.get("results", [])
                    if results:
                        return results[0].get("formatted_address")
    except Exception as exc:
        logger.warning("Google Reverse Geocoding request failed: %s", exc)

    return None


async def driving_distance_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    api_key: Optional[str] = None,
) -> Tuple[Optional[float], str]:
    """
    Calculate driving distance using Google Maps:
      1. Google Routes API (New)
      2. Google Directions API
    Returns:
      (distance_km, provider_name)
    """
    key = api_key or get_google_maps_api_key()

    if key:
        # 1. Try Google Routes API (v2)
        dist, _ = await compute_routes_api({"lat": lat1, "lon": lon1}, {"lat": lat2, "lon": lon2}, api_key=key)
        if dist is not None and dist >= 0:
            return dist, "google_routes"

        # 2. Try Google Directions API
        dist, _ = await compute_directions_api({"lat": lat1, "lon": lon1}, {"lat": lat2, "lon": lon2}, api_key=key)
        if dist is not None and dist >= 0:
            return dist, "google_directions"

    return None, "none"


async def check_google_maps_services_status(api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Check the current status and accessibility of each Google Maps API,
    providing direct links to enable any unactivated services in Google Cloud Console.
    """
    key = api_key or get_google_maps_api_key()
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    project_id = ""
    if "-" in client_id:
        project_id = client_id.split("-")[0]
    if not project_id:
        project_id = "686766750544"

    def _enable_url(service_name: str) -> str:
        return f"https://console.cloud.google.com/apis/library/{service_name}?project={project_id}"

    status_report: Dict[str, Any] = {
        "api_key_configured": bool(key),
        "api_key_preview": f"{key[:8]}...{key[-4:]}" if len(key) > 12 else ("yes" if key else "missing"),
        "project_id": project_id,
        "services": {},
        "links": {
            "routes_api": _enable_url("routes.googleapis.com"),
            "directions_api": _enable_url("directions-backend.googleapis.com"),
            "geocoding_api": _enable_url("geocoding-backend.googleapis.com"),
            "places_api": _enable_url("places.googleapis.com"),
            "credentials_console": f"https://console.cloud.google.com/apis/credentials?project={project_id}",
            "billing_console": f"https://console.cloud.google.com/billing/linkedaccount?project={project_id}",
        },
    }

    if not key:
        status_report["error"] = "Chưa cấu hình GOOGLE_API_KEY trong .env"
        return status_report

    async with httpx.AsyncClient(timeout=6.0) as client:
        # 1. Test Routes API (New)
        try:
            r_resp = await client.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes",
                json={
                    "origin": {"location": {"latLng": {"latitude": 21.0285, "longitude": 105.8542}}},
                    "destination": {"location": {"latLng": {"latitude": 20.8449, "longitude": 106.6881}}},
                    "travelMode": "DRIVE",
                },
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": key,
                    "X-Goog-FieldMask": "routes.distanceMeters",
                    "Referer": get_google_maps_referer(),
                },
            )
            r_data = r_resp.json() if r_resp.content else {}
            if r_resp.status_code == 200:
                status_report["services"]["routes_api"] = {"enabled": True, "status": "OK", "name": "Routes API (Mới)"}
            else:
                msg = r_data.get("error", {}).get("message", r_resp.text)
                status_report["services"]["routes_api"] = {
                    "enabled": False,
                    "status": r_data.get("error", {}).get("status", str(r_resp.status_code)),
                    "message": msg,
                    "name": "Routes API (Mới)",
                    "enable_url": _enable_url("routes.googleapis.com"),
                }
        except Exception as exc:
            status_report["services"]["routes_api"] = {
                "enabled": False,
                "status": "ERROR",
                "message": str(exc),
                "enable_url": _enable_url("routes.googleapis.com"),
            }

        # 2. Test Directions API
        try:
            d_resp = await client.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params={"origin": "21.0285,105.8542", "destination": "20.8449,106.6881", "mode": "driving", "key": key},
            )
            d_data = d_resp.json() if d_resp.content else {}
            d_status = d_data.get("status")
            status_report["services"]["directions_api"] = {
                "enabled": d_status == "OK",
                "status": d_status or str(d_resp.status_code),
                "message": d_data.get("error_message", ""),
                "name": "Directions API",
                "enable_url": _enable_url("directions-backend.googleapis.com"),
            }
        except Exception as exc:
            status_report["services"]["directions_api"] = {
                "enabled": False,
                "status": "ERROR",
                "message": str(exc),
                "enable_url": _enable_url("directions-backend.googleapis.com"),
            }

        # 3. Test Geocoding API
        try:
            g_resp = await client.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": "Hà Nội", "key": key, "language": "vi"},
            )
            g_data = g_resp.json() if g_resp.content else {}
            g_status = g_data.get("status")
            status_report["services"]["geocoding_api"] = {
                "enabled": g_status == "OK",
                "status": g_status or str(g_resp.status_code),
                "message": g_data.get("error_message", ""),
                "name": "Geocoding API",
                "enable_url": _enable_url("geocoding-backend.googleapis.com"),
            }
        except Exception as exc:
            status_report["services"]["geocoding_api"] = {
                "enabled": False,
                "status": "ERROR",
                "message": str(exc),
                "enable_url": _enable_url("geocoding-backend.googleapis.com"),
            }

    return status_report
