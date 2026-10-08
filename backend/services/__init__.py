"""Core Services Domain Module — Database connection pool, system configuration, settings, telegram, data retention, process orchestrator."""
from backend.services.config import setup_logging, normalize_plate, trigger_ai_reload
from backend.services.database import DatabaseManager
from backend.services.settings_service import (
    get_app_setting,
    set_app_setting,
    set_many_app_settings,
    get_all_app_settings,
)
from backend.services.telegram_service import notify_telegram, notify_telegram_video
from backend.services.retention_manager import RetentionManager
from backend.services.orchestrator import CameraOrchestrator

__all__ = [
    "setup_logging",
    "normalize_plate",
    "trigger_ai_reload",
    "DatabaseManager",
    "get_app_setting",
    "set_app_setting",
    "set_many_app_settings",
    "get_all_app_settings",
    "notify_telegram",
    "notify_telegram_video",
    "RetentionManager",
    "CameraOrchestrator",
]
