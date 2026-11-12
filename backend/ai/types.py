from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InferenceResult:
    cam_id: str
    ts: float
    plate_text: str = ""
    plate_conf: float = 0.0
    plate_type: str = "white"
    face_name: str = ""
    face_conf: float = 0.0
    snapshot_path: str = ""
    annotated_frame: Optional[object] = field(default=None, repr=False)
