from streamlit_app.models.detector_model import get_db


def get_camera_providers() -> dict:
    """Load camera providers from DB."""
    try:
        rows = get_db().get_all_providers()
        return {
            r["provider_key"]: {
                "label": r["name"],
                "method": r.get("notes") or r["name"],
            }
            for r in rows if r.get("is_active", True)
        }
    except Exception:
        return {}


# Module-level aliases kept for backward compatibility with existing imports
CAMERA_PROVIDERS = get_camera_providers()
PROVIDER_TYPES = list(CAMERA_PROVIDERS.keys())


class CameraController:
    """Controller for camera management logic."""

    @staticmethod
    def get_cameras():
        return get_db().get_all_cameras()

    @staticmethod
    def get_all_cameras():
        return CameraController.get_cameras()

    @staticmethod
    def get_active_cameras():
        return [c for c in CameraController.get_cameras() if c.get('is_active', True)]

    @staticmethod
    def get_camera(camera_id: int):
        return get_db().get_camera(camera_id)

    @staticmethod
    def add_camera(name: str, cam_type: str, imou_device_id: str, rtsp_url: str, is_active: bool = True,
                   imou_channel_id: str = "0", site_ids=None, provider_keys: dict = None) -> bool:
        return get_db().add_camera(name, cam_type, imou_device_id, rtsp_url, is_active, imou_channel_id, site_ids, provider_keys)

    @staticmethod
    def update_camera(camera_id: int, name: str, cam_type: str, imou_device_id: str, rtsp_url: str, is_active: bool,
                      imou_channel_id: str = "0", site_ids=None, provider_keys: dict = None) -> bool:
        return get_db().update_camera(camera_id, name, cam_type, imou_device_id, rtsp_url, is_active, imou_channel_id, site_ids, provider_keys)

    @staticmethod
    def delete_camera(camera_id: int) -> bool:
        return get_db().delete_camera(camera_id)

    @staticmethod
    def get_sites():
        return get_db().get_sites()

    @staticmethod
    def get_imou_api_keys():
        return get_db().get_imou_api_keys()

    @staticmethod
    def update_imou_api_keys(app_id: str, app_secret: str) -> bool:
        return get_db().update_imou_api_keys(app_id, app_secret)

    @staticmethod
    def get_provider_api_keys(provider: str) -> dict:
        return get_db().get_provider_api_keys(provider)

    @staticmethod
    def update_provider_api_keys(provider: str, data: dict) -> bool:
        return get_db().update_provider_api_keys(provider, data)

camera_controller = CameraController()
