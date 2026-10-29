from typing import TypedDict, Optional


class SessionUser(TypedDict, total=False):
    id: int
    username: str
    role: str
    full_name: str
    allowed_menus: str
    site_ids: list[int]
    company_ids: list[int]


class CameraConfig(TypedDict, total=False):
    id: int
    camera_name: str
    camera_type: str
    rtsp_url: Optional[str]
    site_ids: list[int]
    is_active: bool
    is_streaming: bool
    camera_ip: Optional[str]
    camera_port: int


class PlateLog(TypedDict, total=False):
    id: int
    cam_id: str
    plate_text: str
    plate_conf: float
    plate_type: str
    snapshot_path: str
    logged_at: str


class InvoiceCompany(TypedDict, total=False):
    id: int
    name: str
    tax_code: str
    address: str
    email: Optional[str]


class PlateCandidateDict(TypedDict, total=False):
    method: str
    raw_text: str
    cleaned_text: str
    conf: float
    score: float
    is_valid_vn: bool
