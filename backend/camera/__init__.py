"""Camera Domain Module — RTSP stream grabber, rolling & event video recorder, camera services."""
from backend.camera.camera_service import (
    get_all_cameras,
    get_active_cameras,
    get_camera,
    add_camera,
    update_camera,
    set_camera_streaming,
    get_camera_rtsp_url,
)
from backend.camera.grabber import grabber_process
from backend.camera.recorder import recorder_worker, RecorderProcess

__all__ = [
    "get_all_cameras",
    "get_active_cameras",
    "get_camera",
    "add_camera",
    "update_camera",
    "set_camera_streaming",
    "get_camera_rtsp_url",
    "grabber_process",
    "recorder_worker",
    "RecorderProcess",
]
