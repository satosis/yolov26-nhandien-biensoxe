import logging
import shutil
import os
import time
import urllib.parse
import bcrypt
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from typing import Literal, Optional
from frontend.deps import get_menu_context, require_menu, templates
from backend.services.database import db
from backend.services import config as cfg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users")
_SITE_IDS_PATTERN = r"^\s*(?:[1-9]\d{0,9}(?:\s*,\s*[1-9]\d{0,9})*)?\s*$"
_MAX_DB_INTEGER = 2_147_483_647


def _parsed_site_ids(value: str | None) -> list[int] | None:
    if not value or not value.strip():
        return None
    return [int(site_id.strip()) for site_id in value.split(",")]


def get_all_users(limit=None, offset=None) -> list[dict]:
    query = """
        SELECT u.id, u.username, r.role_name as role, u.created_at, u.full_name, u.note, u.site_ids, u.person_identity, u.kyc_level
        FROM users u
        LEFT JOIN roles r ON u.role_id = r.id
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


def get_user_by_id(user_id: int) -> dict | None:
    query = """
        SELECT u.id, u.username, u.full_name, u.role_id, u.site_ids, u.note, u.person_identity, r.role_name as role 
        FROM users u 
        JOIN roles r ON u.role_id = r.id 
        WHERE u.id = %s
    """
    return db.execute_query(query, (user_id,), fetch=True)


def create_user_record(username: str, hashed_password: str, role_name: str,
                       full_name: str = None, note: str = None, site_ids: list | None = None) -> tuple[bool, str]:
    existing = db.select("users", columns="id", conditions={"username": username}, fetchall=False)
    if existing:
        return False, f"Username '{username}' đã tồn tại."
    query = """
        INSERT INTO users (username, hashed_password, role_id, full_name, note, site_ids)
        VALUES (%s, %s, (SELECT id FROM roles WHERE role_name = %s LIMIT 1), %s, %s, %s)
        RETURNING id
    """
    new_id = db.execute_query(
        query, (username, hashed_password, role_name, full_name, note, site_ids),
        commit=True, return_id=True,
    )
    return (True, "") if new_id is not None else (False, "Không thể tạo người dùng.")


def update_user_record(user_id: int, username: str, role_name: str, hashed_password: str = None,
                       full_name: str = None, note: str = None, site_ids: list | None = None) -> bool:
    if hashed_password:
        query = """
            UPDATE users
            SET username=%s,
                role_id=(SELECT id FROM roles WHERE role_name=%s LIMIT 1),
                hashed_password=%s,
                full_name=%s,
                note=%s,
                site_ids=%s
            WHERE id=%s
        """
        params = (username, role_name, hashed_password, full_name, note, site_ids, user_id)
    else:
        query = """
            UPDATE users
            SET username=%s,
                role_id=(SELECT id FROM roles WHERE role_name=%s LIMIT 1),
                full_name=%s,
                note=%s,
                site_ids=%s
            WHERE id=%s
        """
        params = (username, role_name, full_name, note, site_ids, user_id)
    return db.execute_query(query, params, commit=True)


def delete_user_record(user_id: int) -> bool:
    return db.delete("users", {"id": user_id})


@router.get("")
async def users_page(request: Request, error: Optional[str] = None, user: dict = Depends(require_menu("users"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "users"
    users = get_all_users()
    
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
    ctx["roles"] = db.select("roles", order_by="id ASC")
    ctx["error_msg"] = error
    
    require_kyc_setting = db.select("app_settings", conditions={"key": "REQUIRE_KYC"}, fetchall=False)
    ctx["require_kyc"] = require_kyc_setting and require_kyc_setting.get("value", "false").lower() == "true"
    
    return templates.TemplateResponse("pages/users.html", {"request": request, **ctx})


@router.post("")
async def create_user(
    request: Request,
    user: dict = Depends(require_menu("users")),
    username: str = Form(...),
    password: str = Form(...),
    full_name: Optional[str] = Form(None),
    role_name: str = Form(...),
    site_ids: Optional[str] = Form(None, pattern=_SITE_IDS_PATTERN),
    note: Optional[str] = Form(None),
):
    parsed_site_ids = _parsed_site_ids(site_ids)
    if parsed_site_ids and any(site_id > _MAX_DB_INTEGER for site_id in parsed_site_ids):
        return HTMLResponse("ID địa điểm không hợp lệ.", status_code=422)

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    ok, msg = create_user_record(
        username=username,
        hashed_password=hashed,
        role_name=role_name,
        full_name=full_name or None,
        note=note or None,
        site_ids=parsed_site_ids,
    )
    if not ok:
        return RedirectResponse(url=f"/admin/users?error={urllib.parse.quote_plus(msg)}", status_code=303)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/{user_id}/edit")
async def edit_user(
    user_id: int,
    request: Request,
    user: dict = Depends(require_menu("users")),
    username: str = Form(...),
    password: Optional[str] = Form(None),
    full_name: Optional[str] = Form(None),
    role_name: str = Form(...),
    site_ids: Optional[str] = Form(None, pattern=_SITE_IDS_PATTERN),
    note: Optional[str] = Form(None),
):
    parsed_site_ids = _parsed_site_ids(site_ids)
    if parsed_site_ids and any(site_id > _MAX_DB_INTEGER for site_id in parsed_site_ids):
        return HTMLResponse("ID địa điểm không hợp lệ.", status_code=422)

    hashed = None
    if password:
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    updated = update_user_record(
        user_id=user_id,
        username=username,
        role_name=role_name,
        hashed_password=hashed,
        full_name=full_name or None,
        note=note or None,
        site_ids=parsed_site_ids,
    )
    if not updated:
        return HTMLResponse("Không tìm thấy người dùng.", status_code=404)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    user: dict = Depends(require_menu("users")),
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
    reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
    with open(reload_trigger, "w") as f:
        f.write(str(time.time()))

    return {"ok": True}


@router.post("/{user_id}/face")
async def upload_face(
    user_id: int,
    request: Request,
    face_image: UploadFile = File(...),
    angle: Literal["front", "left", "right", "up", "down"] = Form("front"),
    user: dict = Depends(require_menu("users")),
):
    target_user = get_user_by_id(user_id)
    if not target_user:
        return JSONResponse(
            {"ok": False, "error": "user_not_found", "message": "Không tìm thấy người dùng."},
            status_code=404,
        )

    identity = str(user_id)
    person_dir = os.path.join(cfg.KNOWN_FACES_DIR, identity)
    os.makedirs(person_dir, exist_ok=True)

    file_path = os.path.join(person_dir, f"{angle}.jpg")
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(face_image.file, buffer)

    invalidate_face_cache()

    reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
    with open(reload_trigger, "w") as f:
        f.write(str(time.time()))

    logger.info("Face uploaded for user %s (%s), angle=%s. Reload triggered.", user_id, identity, angle)
    
    return JSONResponse({"ok": True, "angle": angle, "identity": identity})
