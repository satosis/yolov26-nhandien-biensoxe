import json
import logging
import urllib.parse

from backend.services.database import db
from backend.services.settings_service import get_app_setting, set_many_app_settings

_SITE_FILTER = "(%s::integer IS NULL OR %s::integer = ANY(site_ids))"
logger = logging.getLogger(__name__)


def _provider_keys_mapping(value: dict | str | None) -> dict:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("Camera provider_keys must be a JSON object")
    return value


def get_all_cameras(site_id: int | None = None) -> list[dict]:
    return db.execute_query(f"SELECT * FROM cameras WHERE {_SITE_FILTER} ORDER BY id", (site_id, site_id), fetchall=True)


def get_active_cameras(site_id: int | None = None) -> list[dict]:
    return db.execute_query(f"SELECT * FROM cameras WHERE is_active AND {_SITE_FILTER} ORDER BY id", (site_id, site_id), fetchall=True)


def get_camera(camera_id: int) -> dict | None:
    return db.select("cameras", conditions={"id": camera_id}, fetchall=False)


def add_camera(name, cam_type, imou_device_id, rtsp_url, is_active=True,
               imou_channel_id="0", site_ids=None, provider_keys=None,
               camera_ip=None, camera_port=554) -> int | None:
    query = """
        INSERT INTO cameras (camera_name, camera_type, imou_device_id, imou_channel_id,
                            rtsp_url, is_active, site_ids, provider_keys, camera_ip, camera_port)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    pk_json = json.dumps(_provider_keys_mapping(provider_keys))
    dev_id = imou_device_id.strip() if imou_device_id else None
    params = (name, cam_type, dev_id, imou_channel_id, rtsp_url, is_active, site_ids, pk_json, camera_ip, camera_port)
    camera_id = db._execute_query(query, params, commit=True, return_id=True)
    if isinstance(camera_id, bool) or not isinstance(camera_id, int) or camera_id <= 0:
        logger.error("Camera insert returned no valid ID")
        return None
    return camera_id


def update_camera(camera_id, name, cam_type, imou_device_id, rtsp_url,
                  is_active, imou_channel_id="0", site_ids=None, provider_keys=None,
                  camera_ip=None, camera_port=None) -> bool:
    query = """
        UPDATE cameras
        SET camera_name=%s, camera_type=%s, imou_device_id=%s, imou_channel_id=%s,
            rtsp_url=%s, is_active=%s, site_ids=%s, provider_keys=%s,
            camera_ip=%s, camera_port=%s, updated_at=NOW()
        WHERE id=%s
    """
    pk_json = json.dumps(_provider_keys_mapping(provider_keys))
    dev_id = imou_device_id.strip() if imou_device_id else None
    params = (name, cam_type, dev_id, imou_channel_id, rtsp_url, is_active, site_ids, pk_json, camera_ip, camera_port, camera_id)
    return db._execute_query(query, params, commit=True)


def set_camera_streaming(camera_id: int, is_streaming: bool) -> bool:
    return db.update("cameras", {"is_streaming": is_streaming}, {"id": camera_id})


def delete_camera(camera_id: int) -> bool:
    return db.delete("cameras", {"id": camera_id})


def upsert_camera_asset(cam_id: str, name: str, ip: str = None,
                        serial: str = None, model: str = None,
                        firmware: str = None, rtsp_url: str = None,
                        onvif_port: int = None, onvif_profiles: list = None,
                        mac: str = None) -> bool:
    profiles_json = json.dumps(onvif_profiles) if onvif_profiles else None
    query = """
        INSERT INTO cameras_asset (cam_id, name, ip, mac, serial, model, firmware, rtsp_url, onvif_port, onvif_profiles, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ON CONFLICT (cam_id) DO UPDATE SET
            ip = COALESCE(EXCLUDED.ip, cameras_asset.ip),
            mac = COALESCE(EXCLUDED.mac, cameras_asset.mac),
            serial = COALESCE(EXCLUDED.serial, cameras_asset.serial),
            model = COALESCE(EXCLUDED.model, cameras_asset.model),
            firmware = COALESCE(EXCLUDED.firmware, cameras_asset.firmware),
            name = COALESCE(EXCLUDED.name, cameras_asset.name),
            rtsp_url = COALESCE(EXCLUDED.rtsp_url, cameras_asset.rtsp_url),
            onvif_port = COALESCE(EXCLUDED.onvif_port, cameras_asset.onvif_port),
            onvif_profiles = COALESCE(EXCLUDED.onvif_profiles, cameras_asset.onvif_profiles),
            updated_at = NOW()
    """
    written = db._execute_query(query, (cam_id, name, ip, mac, serial, model, firmware,
                                        rtsp_url, onvif_port, profiles_json), commit=True)
    if not written:
        logger.error("Camera asset write affected no row")
    return written


def store_onvif_detected_rtsp(camera_id: int, rtsp_url: str) -> bool:
    written = db._execute_query(
        "UPDATE cameras SET onvif_rtsp_detected = %s, updated_at = NOW() WHERE id = %s",
        (rtsp_url, camera_id), commit=True
    )
    if not written:
        logger.error("Camera %s ONVIF RTSP write affected no row", camera_id)
    return written


def regenerate_rtsp_url(camera_id: int) -> bool:
    written = db._execute_query(
        "UPDATE cameras SET rtsp_url = NULL, onvif_rtsp_detected = NULL, updated_at = NOW() WHERE id = %s",
        (camera_id,), commit=True
    )
    if not written:
        logger.error("Camera %s RTSP reset affected no row", camera_id)
    return written


def get_camera_rtsp_url(camera_id: int, subtype: int = 1) -> str | None:
    cam = get_camera(camera_id)
    if not cam:
        return None

    onvif_url = cam.get('onvif_rtsp_detected')
    if onvif_url and onvif_url.startswith("rtsp://"):
        return onvif_url

    manual_url = cam.get('rtsp_url')
    is_placeholder = "admin:password@192.168.1.100" in (manual_url or "")
    is_broken = "@@" in (manual_url or "")
    if manual_url and manual_url.startswith("rtsp://") and not is_placeholder and not is_broken:
        return manual_url

    ip = cam.get('camera_ip')
    if not ip:
        return None

    cam_type = cam.get('camera_type', 'rtsp')
    provider = get_provider(cam_type)
    template = (provider.get('rtsp_template') if provider else None) or 'rtsp://{user}:{password}@{ip}:{port}/stream'

    provider_keys = _provider_keys_mapping(cam.get('provider_keys'))

    user = provider_keys.get('user', 'admin')
    mk = provider_keys.get('mk')
    if not mk:
        mk = get_app_setting(f"{cam_type.upper()}_MK", "") or get_app_setting("IMOU_MK", "")
    if not mk:
        return None

    encoded_mk = urllib.parse.quote(mk, safe='')
    port = cam.get('camera_port') or 554
    channel = cam.get('imou_channel_id') or '1'
    global_subtype = get_app_setting("IMOU_SUBTYPE", str(subtype))

    return template.format(
        user=user,
        password=encoded_mk,
        ip=ip,
        port=port,
        channel=channel,
        subtype=global_subtype
    )


def get_cameras_latest_status() -> dict:
    query = """
        SELECT DISTINCT ON (camera_id) camera_id, status, logged_at
        FROM camera_status_log
        ORDER BY camera_id, logged_at DESC
    """
    rows = db.execute_query(query, fetchall=True)
    return {str(r['camera_id']): r for r in rows}


def get_camera_health(cam_id: str = None, hours: int = 24) -> list[dict]:
    if cam_id:
        query = "SELECT * FROM camera_status_log WHERE camera_id = %s AND logged_at >= NOW() - (%s * INTERVAL '1 hour') ORDER BY logged_at DESC"
        params = (cam_id, hours)
    else:
        query = "SELECT * FROM camera_status_log WHERE logged_at >= NOW() - (%s * INTERVAL '1 hour') ORDER BY logged_at DESC"
        params = (hours,)
    return db.execute_query(query, params, fetchall=True)


def get_all_providers() -> list:
    return db.select("camera_providers", order_by="name ASC")


def get_provider(provider_key: str) -> dict | None:
    return db.select("camera_providers", conditions={"provider_key": provider_key}, fetchall=False)


def upsert_provider(provider_key: str, name: str, api_base_url: str = None,
                    api_doc_url: str = None, rtsp_template: str = None,
                    onvif_port: int = 80, default_rtsp_port: int = 554,
                    default_http_port: int = 80, notes: str = None,
                    logo_url: str = None, is_active: bool = True) -> bool:
    query = """
        INSERT INTO camera_providers
            (provider_key, name, api_base_url, api_doc_url, rtsp_template,
             onvif_port, default_rtsp_port, default_http_port, notes, logo_url, is_active, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (provider_key) DO UPDATE SET
            name = EXCLUDED.name,
            api_base_url = EXCLUDED.api_base_url,
            api_doc_url = EXCLUDED.api_doc_url,
            rtsp_template = EXCLUDED.rtsp_template,
            onvif_port = EXCLUDED.onvif_port,
            default_rtsp_port = EXCLUDED.default_rtsp_port,
            default_http_port = EXCLUDED.default_http_port,
            notes = EXCLUDED.notes,
            logo_url = EXCLUDED.logo_url,
            is_active = EXCLUDED.is_active,
            updated_at = NOW()
    """
    return db._execute_query(
        query,
        (provider_key, name, api_base_url, api_doc_url, rtsp_template,
         onvif_port, default_rtsp_port, default_http_port, notes, logo_url, is_active),
        commit=True
    )


def delete_provider(provider_key: str) -> bool:
    return db.delete("camera_providers", {"provider_key": provider_key})


def get_provider_api_keys(provider: str) -> dict:
    prefix = f"{provider.upper()}_"
    rows = db.execute_query(
        "SELECT key, value FROM app_settings WHERE key LIKE %s",
        (f"{prefix}%",), fetchall=True
    )
    result = {}
    for r in rows:
        field_name = r['key'][len(prefix):].lower()
        result[field_name] = r['value']
    return result


def update_provider_api_keys(provider: str, data: dict) -> bool:
    prefix = f"{provider.upper()}_"
    new_keys = {f"{prefix}{k.upper()}": v for k, v in data.items() if v is not None}
    return set_many_app_settings(new_keys)


def get_imou_api_keys() -> dict:
    provider_keys = get_provider_api_keys("imou")
    app_id = provider_keys.get('open_app_id', '')
    app_secret = provider_keys.get('open_app_secret', '')
    if not app_id or not app_secret:
        rows = db.execute_query(
            "SELECT key, value FROM app_settings WHERE key IN ('IMOU_APP_ID', 'IMOU_APP_SECRET')",
            fetchall=True
        )
        res = {k['key']: k['value'] for k in rows}
        if not app_id:
            app_id = res.get('IMOU_APP_ID', '')
        if not app_secret:
            app_secret = res.get('IMOU_APP_SECRET', '')
    return {'app_id': app_id, 'app_secret': app_secret}


def update_imou_api_keys(app_id: str, app_secret: str) -> bool:
    return set_many_app_settings({'IMOU_APP_ID': app_id, 'IMOU_APP_SECRET': app_secret})
