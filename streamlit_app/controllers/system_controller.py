import os
from typing import Any, List
from streamlit_app.models.detector_model import get_db

class SystemController:
    @staticmethod
    def get_app_setting(key: str, default: Any = None) -> Any:
        val = get_db().get_app_setting(key)
        return val if val is not None else os.environ.get(key, default)

    @staticmethod
    def set_app_setting(key: str, value: str) -> bool:
        return get_db().set_app_setting(key, value)

    @staticmethod
    def set_many_app_settings(data: dict) -> bool:
        return get_db().set_many_app_settings(data)

    @staticmethod
    def get_low_confidence_events(limit: int = 10, max_conf: float = 0.6, hours_back: int = 24) -> List[dict]:
        return get_db().get_low_confidence_events(limit, max_conf, hours_back)

    @staticmethod
    def mark_event_audited(event_id: str, gemini_result: str, is_mismatch: bool = False) -> bool:
        return get_db().mark_event_audited(event_id, gemini_result, is_mismatch)

system_controller = SystemController()
