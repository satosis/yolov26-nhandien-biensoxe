import logging
import shutil
import os
import tempfile
import urllib.parse
import bcrypt
import cv2
import numpy as np
from psycopg2.errors import UniqueViolation
from fastapi import APIRouter, Request, Depends, Form, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from typing import Literal, Optional
from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate_counts
from backend.services.database import db
from backend.services import config as cfg
from backend.util.image_utils import decode_image
from frontend.modules.user_nicknames import clean_nickname
from frontend.modules.gender_utils import predict_gender

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users")
_SITE_IDS_PATTERN = r"^\s*(?:[1-9]\d{0,9}(?:\s*,\s*[1-9]\d{0,9})*)?\s*$"
_MAX_DB_INTEGER = 2_147_483_647
_MAX_FACE_UPLOAD_BYTES = 10 * 1024 * 1024


def _parsed_site_ids(value: str | None) -> list[int] | None:
    if not value or not value.strip():
        return None
    return [int(site_id.strip()) for site_id in value.split(",")]


def get_all_users(limit=None, offset=None) -> list[dict]:
    query = """
        SELECT u.id, u.username, r.role_name as role, u.created_at, u.full_name, u.gender, u.nickname, u.phone, u.note, u.site_ids, u.company_ids, u.person_identity, u.kyc_level,
               u.email, u.bank_account, u.bank_name,
               p.job_title, p.department
        FROM users u
        LEFT JOIN roles r ON u.role_id = r.id
        LEFT JOIN staff_salary_profiles p ON p.user_id = u.id
        ORDER BY u.id
    """
    params = []
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    return db.execute_query(query, tuple(params) if params else None, fetchall=True)


def count_all_users() -> int:
    row = db.execute_query("SELECT COUNT(*) AS total FROM users", fetch=True)
    return int((row or {}).get("total") or 0)


def get_user_by_id(user_id: int) -> dict | None:
    query = """
        SELECT u.id, u.username, u.full_name, u.gender, u.nickname, u.phone, u.role_id, u.site_ids, u.company_ids, u.note, u.person_identity,
               u.email, u.bank_account, u.bank_name, r.role_name as role,
               p.job_title, p.department
        FROM users u 
        LEFT JOIN roles r ON u.role_id = r.id
        LEFT JOIN staff_salary_profiles p ON p.user_id = u.id
        WHERE u.id = %s
    """
    return db.execute_query(query, (user_id,), fetch=True)


def create_user_record(username: str, hashed_password: str, role_name: str,
                       full_name: str = None, gender: str = None, nickname: str = "", phone: str = None, email: str = None,
                       bank_account: str = None, bank_name: str = None,
                       note: str = None, site_ids: list | None = None,
                       company_ids: list | None = None) -> tuple[bool, str]:
    existing = db.select("users", columns="id", conditions={"username": username}, fetchall=False)
    if existing:
        return False, f"Username '{username}' đã tồn tại."
    if not gender or gender not in ("nam", "nữ"):
        gender = predict_gender(full_name)
    query = """
        INSERT INTO users (username, hashed_password, role_id, full_name, gender, nickname, phone, email, bank_account, bank_name, note, site_ids, company_ids)
        VALUES (%s, %s, (SELECT id FROM roles WHERE role_name = %s LIMIT 1), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    new_id = db.execute_query(
        query, (username, hashed_password, role_name, full_name, gender, clean_nickname(nickname), phone, email, bank_account, bank_name, note, site_ids, company_ids),
        commit=True, return_id=True,
    )
    return (True, "") if new_id is not None else (False, "Không thể tạo người dùng.")


def update_user_record(user_id: int, username: str, role_name: str, hashed_password: str = None,
                       full_name: str = None, gender: str = None, nickname: str = "", phone: str = None, email: str = None,
                       bank_account: str = None, bank_name: str = None,
                       note: str = None, site_ids: list | None = None,
                       company_ids: list | None = None) -> bool:
    nickname = clean_nickname(nickname)
    if not gender or gender not in ("nam", "nữ"):
        gender = predict_gender(full_name)
    if hashed_password:
        query = """
            UPDATE users
            SET username=%s,
                role_id=(SELECT id FROM roles WHERE role_name=%s LIMIT 1),
                hashed_password=%s,
                full_name=%s,
                gender=%s,
                nickname=%s,
                phone=%s,
                email=%s,
                bank_account=%s,
                bank_name=%s,
                note=%s,
                site_ids=%s,
                company_ids=%s,
                updated_at=NOW()
            WHERE id=%s
        """
        params = (username, role_name, hashed_password, full_name, gender, nickname, phone, email, bank_account, bank_name, note, site_ids, company_ids, user_id)
    else:
        query = """
            UPDATE users
            SET username=%s,
                role_id=(SELECT id FROM roles WHERE role_name=%s LIMIT 1),
                full_name=%s,
                gender=%s,
                nickname=%s,
                phone=%s,
                email=%s,
                bank_account=%s,
                bank_name=%s,
                note=%s,
                site_ids=%s,
                company_ids=%s,
                updated_at=NOW()
            WHERE id=%s
        """
        params = (username, role_name, full_name, gender, nickname, phone, email, bank_account, bank_name, note, site_ids, company_ids, user_id)
    return db.execute_query(query, params, commit=True)


def delete_user_record(user_id: int) -> bool:
    return db.delete("users", {"id": user_id})


@router.get("")
async def users_page(
    request: Request,
    page: int = Query(1, ge=1),
    error: Optional[str] = None,
    user: dict = Depends(require_menu("users")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "users"
    total_users = count_all_users()
    paginated = paginate_counts(total_users, page=page, per_page=20, base_url="/admin/users")
    users = get_all_users(limit=paginated["per_page"], offset=(paginated["page"] - 1) * paginated["per_page"]) or []
    
    for u in users:
        identities = [
            u.get("person_identity"),
            u.get("username"),
            str(u.get("id"))
        ]
        
        u["has_face"] = False
        for identity in identities:
            if identity:
                identity_dir = os.path.join(cfg.KNOWN_FACES_DIR, str(identity))
                if os.path.isdir(identity_dir):
                    for f in os.listdir(identity_dir):
                        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                            u["has_face"] = True
                            break
                if u["has_face"]:
                    break
        
        logger.info("User %s has_face=%s (Checked: %s)", u.get("username"), u["has_face"], identities)
        
    ctx["all_users"] = users
    ctx["page"] = paginated["page"]
    ctx["total_pages"] = paginated["total_pages"]
    ctx["total"] = paginated["total"]
    ctx["page_base_url"] = paginated["page_base_url"]
    ctx["roles"] = db.select("roles", order_by="id ASC")
    ctx["error_msg"] = error

    from frontend.modules.invoices import list_companies
    companies = list_companies()
    ctx["all_companies"] = companies
    ctx["companies_map"] = {c["id"]: c for c in companies}
    
    require_kyc_setting = db.select("app_settings", conditions={"key": "REQUIRE_KYC"}, fetchall=False)
    ctx["require_kyc"] = require_kyc_setting and require_kyc_setting.get("value", "false").lower() == "true"
    
    return templates.TemplateResponse("pages/users/index.html", {"request": request, **ctx})


@router.get("/add")
async def add_user_page(
    request: Request,
    error: Optional[str] = None,
    user: dict = Depends(require_menu("users")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "users"
    ctx["roles"] = db.select("roles", order_by="id ASC") or []
    from frontend.modules.invoices import list_companies
    companies = list_companies()
    ctx["all_companies"] = companies
    ctx["companies_map"] = {c["id"]: c for c in companies}
    ctx["sites"] = db.select("sites", order_by="id ASC") or []
    ctx["error_msg"] = error
    return templates.TemplateResponse("pages/users/add.html", {"request": request, **ctx})


@router.post("")
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    full_name: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    nickname: str = Form(""),
    phone: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    bank_account: Optional[str] = Form(None),
    bank_name: Optional[str] = Form(None),
    role_name: str = Form(...),
    site_ids: Optional[str] = Form(None, pattern=_SITE_IDS_PATTERN),
    note: Optional[str] = Form(None),
    _: dict = Depends(require_menu("users")),
):
    parsed_site_ids = _parsed_site_ids(site_ids)
    try:
        nickname = clean_nickname(nickname)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    if parsed_site_ids and any(site_id > _MAX_DB_INTEGER for site_id in parsed_site_ids):
        return HTMLResponse("ID địa điểm không hợp lệ.", status_code=422)

    form_data = await request.form()
    raw_companies = form_data.getlist("company_ids")
    company_ids = [int(c) for c in raw_companies if str(c).strip().isdigit()]

    user_gender = gender.strip().lower() if gender and gender.strip().lower() in ("nam", "nữ") else predict_gender(full_name)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    ok, msg = create_user_record(
        username=username.strip(),
        hashed_password=hashed,
        role_name=role_name,
        full_name=full_name.strip() if full_name else None,
        gender=user_gender,
        nickname=nickname,
        phone=phone.strip() if phone else None,
        email=email.strip() if email else None,
        bank_account=bank_account.strip() if bank_account else None,
        bank_name=bank_name.strip() if bank_name else None,
        note=note.strip() if note else None,
        site_ids=parsed_site_ids,
        company_ids=company_ids or None,
    )
    if not ok:
        return RedirectResponse(url=f"/admin/users/add?error={urllib.parse.quote_plus(msg)}", status_code=303)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.get("/{user_id}/edit")
async def edit_user_page(
    user_id: int,
    request: Request,
    error: Optional[str] = None,
    user: dict = Depends(require_menu("users")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "users"
    target_user = get_user_by_id(user_id)
    if not target_user:
        return RedirectResponse(url="/admin/users?error=Không+tìm+thấy+người+dùng.", status_code=303)

    ctx["target_user"] = target_user
    ctx["roles"] = db.select("roles", order_by="id ASC") or []
    from frontend.modules.invoices import list_companies
    companies = list_companies()
    ctx["all_companies"] = companies
    ctx["companies_map"] = {c["id"]: c for c in companies}
    ctx["sites"] = db.select("sites", order_by="id ASC") or []
    ctx["error_msg"] = error
    return templates.TemplateResponse("pages/users/edit.html", {"request": request, **ctx})


@router.post("/{user_id}/edit")
async def edit_user(
    user_id: int,
    request: Request,
    username: str = Form(...),
    password: Optional[str] = Form(None),
    full_name: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    nickname: str = Form(""),
    phone: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    bank_account: Optional[str] = Form(None),
    bank_name: Optional[str] = Form(None),
    role_name: str = Form(...),
    site_ids: Optional[str] = Form(None, pattern=_SITE_IDS_PATTERN),
    note: Optional[str] = Form(None),
    _: dict = Depends(require_menu("users")),
):
    parsed_site_ids = _parsed_site_ids(site_ids)
    try:
        nickname = clean_nickname(nickname)
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    if parsed_site_ids and any(site_id > _MAX_DB_INTEGER for site_id in parsed_site_ids):
        return HTMLResponse("ID địa điểm không hợp lệ.", status_code=422)

    form_data = await request.form()
    raw_companies = form_data.getlist("company_ids")
    company_ids = [int(c) for c in raw_companies if str(c).strip().isdigit()]

    hashed = None
    if password and password.strip():
        hashed = bcrypt.hashpw(password.strip().encode(), bcrypt.gensalt()).decode()

    user_gender = gender.strip().lower() if gender and gender.strip().lower() in ("nam", "nữ") else predict_gender(full_name)
    try:
        updated = update_user_record(
            user_id=user_id,
            username=username.strip(),
            role_name=role_name,
            hashed_password=hashed,
            full_name=full_name.strip() if full_name else None,
            gender=user_gender,
            nickname=nickname,
            phone=phone.strip() if phone else None,
            email=email.strip() if email else None,
            bank_account=bank_account.strip() if bank_account else None,
            bank_name=bank_name.strip() if bank_name else None,
            note=note.strip() if note else None,
            site_ids=parsed_site_ids,
            company_ids=company_ids if company_ids else None,
        )
    except UniqueViolation as exc:
        if exc.diag.constraint_name != "users_username_key":
            raise
        message = "Tên đăng nhập này đã được sử dụng. Vui lòng chọn tên khác."
        return RedirectResponse(
            url=f"/admin/users/{user_id}/edit?error={urllib.parse.quote_plus(message)}",
            status_code=303,
        )
    if not updated:
        return RedirectResponse(url=f"/admin/users/{user_id}/edit?error=Không+tìm+thấy+người+dùng.", status_code=303)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    _: dict = Depends(require_menu("users")),
):
    identity = str(user_id)
    person_dir = os.path.join(cfg.KNOWN_FACES_DIR, identity)
    if os.path.isdir(person_dir):
        shutil.rmtree(person_dir)
        logger.info("Deleted face directory for user %s", user_id)

    if not delete_user_record(user_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy người dùng."}, status_code=404)

    from .kyc_router import invalidate_face_cache

    invalidate_face_cache()
    cfg.trigger_ai_reload()

    return {"ok": True}


@router.post("/bulk-delete")
async def bulk_delete_users(
    request: Request,
    _: dict = Depends(require_menu("users")),
):
    ids = []
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            ids = [int(x) for x in body.get("ids", []) if str(x).isdigit()]
        except Exception:
            ids = []
    else:
        form = await request.form()
        raw_ids = form.getlist("ids") or form.getlist("user_ids")
        ids = [int(x) for x in raw_ids if str(x).isdigit()]

    if not ids:
        return JSONResponse({"ok": False, "message": "Vui lòng chọn ít nhất một người dùng để xóa."}, status_code=400)

    from .kyc_router import invalidate_face_cache

    deleted = 0
    for uid in dict.fromkeys(ids):
        target = get_user_by_id(uid)
        if not target or target.get("username") == "admin":
            continue
        identity = str(uid)
        person_dir = os.path.join(cfg.KNOWN_FACES_DIR, identity)
        if os.path.isdir(person_dir):
            shutil.rmtree(person_dir, ignore_errors=True)
            logger.info("Deleted face directory for user %s", uid)
        if delete_user_record(uid):
            deleted += 1

    if deleted:
        invalidate_face_cache()
        cfg.trigger_ai_reload()
        logger.info("Bulk deleted %s users", deleted)

    return JSONResponse({
        "ok": True,
        "count": deleted,
        "message": f"Đã xóa thành công {deleted} người dùng.",
    })


@router.post("/{user_id}/face")
async def upload_face(
    user_id: int,
    face_image: UploadFile = File(...),
    angle: Literal["front", "left", "right", "up", "down"] = Form("front"),
    _: dict = Depends(require_menu("users")),
):
    target_user = get_user_by_id(user_id)
    if not target_user:
        return JSONResponse(
            {"ok": False, "error": "user_not_found", "message": "Không tìm thấy người dùng."},
            status_code=404,
        )

    contents = await face_image.read(_MAX_FACE_UPLOAD_BYTES + 1)
    if not contents or len(contents) > _MAX_FACE_UPLOAD_BYTES:
        logger.warning("Admin face upload rejected: empty or oversized image for user_id=%s", user_id)
        return JSONResponse(
            {"ok": False, "error": "invalid_image", "message": "Ảnh không hợp lệ hoặc quá lớn."},
            status_code=400,
        )

    image, dbg = decode_image(contents, return_debug=True)
    if image is None:
        logger.warning("Admin face upload rejected: unreadable image for user_id=%s: %s", user_id, dbg.get("error"))
        return JSONResponse(
            {"ok": False, "error": "invalid_image", "message": f"Ảnh không hợp lệ: {dbg.get('error', 'Lỗi giải mã')}"},
            status_code=400,
        )

    encoded_ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 96])
    if not encoded_ok:
        logger.error("Admin face JPEG encoding failed for user_id=%s angle=%s", user_id, angle)
        return JSONResponse(
            {"ok": False, "error": "save_failed", "message": "Không thể lưu ảnh khuôn mặt."},
            status_code=500,
        )
    jpeg_bytes = encoded.tobytes()
    canonical_image = decode_image(jpeg_bytes)
    if canonical_image is None:
        logger.error("Admin face JPEG could not be decoded for user_id=%s angle=%s", user_id, angle)
        return JSONResponse(
            {"ok": False, "error": "save_failed", "message": "Không thể lưu ảnh khuôn mặt."},
            status_code=500,
        )

    from .kyc_router import get_face_app, invalidate_face_cache

    faces = get_face_app().get(canonical_image)
    if not faces:
        logger.warning("Admin face upload rejected: no face for user_id=%s", user_id)
        return JSONResponse(
            {"ok": False, "error": "no_face_detected", "message": "Không phát hiện khuôn mặt trong ảnh."},
            status_code=400,
        )
    embedding = np.asarray(faces[0].normed_embedding, dtype=np.float32)
    if embedding.shape != (512,) or not np.isfinite(embedding).all() or np.linalg.norm(embedding) <= 1e-6:
        logger.warning("Admin face upload rejected: invalid face embedding for user_id=%s", user_id)
        return JSONResponse(
            {"ok": False, "error": "invalid_image", "message": "Ảnh khuôn mặt không hợp lệ."},
            status_code=400,
        )

    identity = str(user_id)
    person_dir = os.path.join(cfg.KNOWN_FACES_DIR, identity)
    file_path = os.path.join(person_dir, f"{angle}.jpg")
    os.makedirs(person_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".face-upload-", dir=person_dir) as temporary_dir:
        temporary_file = os.path.join(temporary_dir, "face.jpg")
        with open(temporary_file, "wb") as buffer:
            buffer.write(jpeg_bytes)
        os.replace(temporary_file, file_path)

    invalidate_face_cache()

    cfg.trigger_ai_reload()

    logger.info("Face uploaded for user %s (%s), angle=%s. Reload triggered.", user_id, identity, angle)
    
    return JSONResponse({"ok": True, "angle": angle, "identity": identity})
