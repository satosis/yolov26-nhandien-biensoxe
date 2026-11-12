import logging
import os
import unicodedata
from difflib import SequenceMatcher
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

    return None, None


async def _geocode_request(params: Dict[str, Any], api_key: Optional[str] = None) -> list:
    """
    One call to the Google Geocoding API (address -> point, or point -> address).
    GET https://maps.googleapis.com/maps/api/geocode/json
    Any failure raises; Google answers HTTP 200 with a status in the body, so a status other than OK raises too.
    """
    async with httpx.AsyncClient(timeout=6.0) as client:
        resp = await client.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={**params, "language": "vi", "key": api_key or get_google_maps_api_key()},
        )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK":
        raise RuntimeError(f"Google Geocoding {data.get('status')}: {data.get('error_message', '')}")
    return data["results"]


async def geocode_address_google(
    address: str,
    api_key: Optional[str] = None,
    raise_errors: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Geocode an address to latitude/longitude using Google Geocoding API.
    Returns None on failure, or raises the error itself when raise_errors is set.
    """
    if not address or not address.strip():
        return None
    try:
        top = (await _geocode_request({"address": address.strip(), "region": "vn"}, api_key))[0]
        loc = top["geometry"]["location"]
    except Exception as exc:
        if raise_errors:
            raise
        logger.warning("Google Geocoding failed for '%s': %s", address, exc)
        return None
    return {
        "lat": float(loc["lat"]),
        "lon": float(loc["lng"]),
        "label": top.get("formatted_address") or address,
        "provider": "google_geocode",
        "place_id": top.get("place_id"),
    }


async def search_places(query: str, bias: Optional[Tuple[float, float]] = None, limit: int = 3,
                        api_key: Optional[str] = None) -> list[Dict[str, Any]]:
    """
    Google Maps' own search box: places matching a name, best first, as {name, address, lat, lon}.
    POST https://places.googleapis.com/v1/places:searchText (Places API New). `bias` = (lat, lon) prefers places
    near that point without excluding the others. [] when nothing is found or the API cannot be used.
    """
    key = api_key or get_google_maps_api_key()
    if not key or not query.strip():
        return []
    body: Dict[str, Any] = {"textQuery": query.strip(), "languageCode": "vi", "regionCode": "VN", "pageSize": limit}
    if bias:
        body["locationBias"] = {"circle": {"center": {"latitude": bias[0], "longitude": bias[1]}, "radius": 50000.0}}
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.post(
            "https://places.googleapis.com/v1/places:searchText", json=body,
            headers={"X-Goog-Api-Key": key, "Referer": get_google_maps_referer(),
                     "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location"})
    if resp.status_code != 200:
        logger.warning("Google Places search HTTP %s: %s", resp.status_code, resp.text[:200])
        return []
    return [{"name": (place.get("displayName") or {}).get("text", ""),
             "address": place.get("formattedAddress", ""),
             "lat": float(place["location"]["latitude"]), "lon": float(place["location"]["longitude"])}
            for place in resp.json().get("places", []) if place.get("location")]


_PLACE_MATCHES: Dict[Tuple[str, str], Optional[Dict[str, str]]] = {}


def _plain(value: str) -> str:
    folded = "".join(char for char in unicodedata.normalize("NFD", value.casefold()) if not unicodedata.combining(char))
    return " ".join(folded.replace("đ", "d").split())


async def _google_match(text: str, near: str = "") -> Optional[Dict[str, str]]:
    """
    The place Google Maps' search finds for `text` ({name, address}), only when its name is clearly the same name: the
    typed text, read against the start of Google's name, is at least 85% alike without accents and case. A few wrong
    characters are forgiven; another place (a company of another legal form, a similar street) is never swapped in.
    `near` is an address that anchors the search ("Xã Bình Nguyên Tỉnh Phú Thọ").
    """
    text = " ".join(str(text or "").split())
    if len(text) < 6 or " " not in text:
        return None
    key = (text, near)
    if key not in _PLACE_MATCHES:
        found = await search_places(f"{text}, {near}" if near else text, limit=1)
        if not found:
            return None  # nothing to remember: the API may have failed
        place, typed = found[0], _plain(text)
        alike = place["name"] and SequenceMatcher(None, typed, _plain(place["name"])[:len(typed) + 4]).ratio() >= 0.85
        if len(_PLACE_MATCHES) > 500:
            _PLACE_MATCHES.clear()
        _PLACE_MATCHES[key] = {"name": place["name"], "address": place["address"]} if alike else None
    return _PLACE_MATCHES[key]


async def canonical_place(text: str) -> Optional[str]:
    """
    The name and address Google knows a bare place name by: "Trung Tâm Ky Thuật Tiêu Chuẩn Đo Lường" becomes
    "Trung tâm Kỹ thuật Tiêu chuẩn Đo lường Chất lượng 1, Nhà E, 8 Hoàng Quốc Việt, Nghĩa Đô, Hà Nội". None when the
    best result is not clearly the same name. Text with an address (a comma) is left to canonical_name.
    """
    if "," in text:
        return None
    match = await _google_match(text)
    return (f"{match['name']}, {match['address']}" if match["address"] else match["name"]) if match else None


async def canonical_name(text: str, near: str = "") -> Optional[str]:
    """Google's spelling of one place name inside an address ("cum công nghiêp binh xuyên" -> "Khu công nghiệp Bình
    Xuyên"), looked up near `near`; None when Google's best result is not clearly the same name."""
    match = await _google_match(text, near)
    return match["name"] if match else None


async def resolve_place(text: str, bias: Optional[Tuple[float, float]] = None) -> Optional[Dict[str, Any]]:
    """
    The point of a place written the way people write it, as {label, lat, lon}, or None.
    An address (a commune or street after a company name) is located by that address: Geocoding of the longest tail
    that is finer than a province. A bare name ("Trung tâm kỹ thuật tiêu chuẩn đo lường") is found like Google Maps'
    search box finds it, preferring places near `bias`. Searching a company name when the text gives its address
    would take a similarly named company elsewhere.
    """
    text = text.strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split(",") if part.strip()]
    for size in range(min(3, len(parts) - 1), 0, -1):
        try:
            top = (await _geocode_request({"address": ", ".join(parts[-size:]), "region": "vn"}))[0]
        except Exception:
            continue
        if {"administrative_area_level_1", "country"} & set(top.get("types") or []):
            continue  # only a province: too coarse to say where the place is
        location = top["geometry"]["location"]
        return {"label": top.get("formatted_address") or ", ".join(parts[-size:]),
                "lat": float(location["lat"]), "lon": float(location["lng"])}
    found = await search_places(text, bias, 1)
    if found:
        place = found[0]
        return {"label": f"{place['name']}, {place['address']}" if place["name"] else place["address"],
                "lat": place["lat"], "lon": place["lon"]}
    try:
        top = (await _geocode_request({"address": text, "region": "vn"}))[0]
    except Exception:
        return None
    location = top["geometry"]["location"]
    return {"label": top.get("formatted_address") or text, "lat": float(location["lat"]), "lon": float(location["lng"])}


async def route_distance_km(origin: str, destination: str) -> Tuple[Optional[float], Dict[str, str]]:
    """
    Driving km between two places written as text. Routes reads an address as it is; a name it cannot place is found
    like Google Maps' search finds it (resolve_place, the destination first so the origin prefers places near it).
    Returns (km or None, {"origin", "destination"} addresses that were found by search, empty when Routes read them).
    """
    km, _ = await compute_routes_api(origin, destination)
    if km is not None and km > 0:
        return km, {}
    end = await resolve_place(destination)
    start = await resolve_place(origin, (end["lat"], end["lon"]) if end else None)
    if end is None and start:
        end = await resolve_place(destination, (start["lat"], start["lon"]))
    if not start or not end:
        return None, {}
    km, _ = await compute_routes_api({"lat": start["lat"], "lon": start["lon"]}, {"lat": end["lat"], "lon": end["lon"]})
    if km is None or km <= 0:
        return None, {}
    return km, {"origin": start["label"], "destination": end["label"]}


async def reverse_geocode_google(
    lat: float,
    lon: float,
    api_key: Optional[str] = None,
) -> Optional[str]:
    """Reverse geocode latitude/longitude to a readable Vietnamese address."""
    return (await _geocode_request({"latlng": f"{lat},{lon}"}, api_key))[0].get("formatted_address")


async def reverse_geocode_admin_google(
    lat: float,
    lon: float,
    api_key: Optional[str] = None,
) -> Tuple[str, str]:
    """
    (ward/commune, province/city) Google names for a point, in the current two-level administrative
    structure: administrative_area_level_2 is the ward/commune and level_1 the province or city.
    Any failure raises.
    """
    for result in await _geocode_request({"latlng": f"{lat},{lon}"}, api_key):
        names: Dict[str, str] = {}
        for component in result.get("address_components", []):
            for kind in ("administrative_area_level_2", "administrative_area_level_1"):
                if kind in component.get("types", []):
                    names.setdefault(kind, component.get("long_name", ""))
        if names.get("administrative_area_level_2") and names.get("administrative_area_level_1"):
            return names["administrative_area_level_2"], names["administrative_area_level_1"]
    raise LookupError(f"Google trả kết quả không có phường/xã và tỉnh cho ({lat}, {lon})")


async def driving_distance_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    api_key: Optional[str] = None,
) -> Tuple[Optional[float], str]:
    """
    Calculate driving distance using the Google Routes API (New).
    Returns:
      (distance_km, provider_name)
    """
    key = api_key or get_google_maps_api_key()

    if key:
        dist, _ = await compute_routes_api({"lat": lat1, "lon": lon1}, {"lat": lat2, "lon": lon2}, api_key=key)
        if dist is not None and dist >= 0:
            return dist, "google_routes"

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

        # 2. Test Geocoding API
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

    status_report["services"]["places_api"] = {
        "enabled": bool(await search_places("Hà Nội", limit=1, api_key=key)),
        "name": "Places API (Mới)",
        "enable_url": _enable_url("places.googleapis.com"),
    }
    return status_report
