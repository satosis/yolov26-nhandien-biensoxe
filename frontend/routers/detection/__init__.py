from .model_registry import (
    _get_models,
    _read_cached_ocr,
    _cleanup_idle_models,
    _cleanup_models_loop,
    _start_cleanup_loop,
    _get_video_jobs,
    _video_identity,
    _owned_video_job,
    _model_lock,
    _video_jobs_lock,
    _detector_lock,
    _ocr_lock,
    _ocr_cache,
)
from .tracking import (
    TrackedPlate,
    SpatialPlateTracker,
    _trackers,
    _trackers_lock,
    _get_spatial_tracker,
)
from .plate_helpers import (
    _check_whitelist,
    _add_pending_plate_simple,
    _authorize_detection_camera,
    _read_latest_camera_frame,
    _format_vn_plate_for_display,
    _realtime_plate_item,
    _detect_realtime_frame,
    _detect_plates,
)
from .pages import detection_page
from .stream import _mjpeg_frame

__all__ = [
    "detection_page",
    "_get_models",
    "_read_cached_ocr",
    "_cleanup_idle_models",
    "_cleanup_models_loop",
    "_start_cleanup_loop",
    "_get_video_jobs",
    "_video_identity",
    "_owned_video_job",
    "TrackedPlate",
    "SpatialPlateTracker",
    "_get_spatial_tracker",
    "_check_whitelist",
    "_add_pending_plate_simple",
    "_authorize_detection_camera",
    "_read_latest_camera_frame",
    "_format_vn_plate_for_display",
    "_realtime_plate_item",
    "_detect_realtime_frame",
    "_detect_plates",
    "_mjpeg_frame",
]
