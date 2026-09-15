"""Users router."""
import logging
import shutil
import os
import time
import bcrypt
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional
from frontend.deps import get_db, get_menu_context, require_menu
from backend.core import config as cfg

import urllib.parse
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("")
async def users_page(request: Request, error: Optional[str] = None, user: dict = Depends(require_menu("users"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "users"
    db = get_db()
    users = db.get_all_users()
    
    # Check face registration status for each user
    for u in users:
        # Priority: person_identity -> username -> id
        # This covers all possible folder naming conventions (old logic vs new)
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
    ctx["roles"] = db.get_roles()
    ctx["error_msg"] = error
    
    # Pass REQUIRE_KYC setting to template
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
    site_ids: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
):
    parsed_site_ids = None
    if site_ids:
        parsed_site_ids = [int(s.strip()) for s in site_ids.split(",") if s.strip()]

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    ok, msg = get_db().create_user(
        username=username,
        hashed_password=hashed,
        role_name=role_name,
        full_name=full_name or None,
        note=note or None,
        site_ids=parsed_site_ids,
    )
    if not ok:
        logger.warning("create_user failed: %s", msg)
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
    site_ids: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
):
    parsed_site_ids = None
    if site_ids:
        parsed_site_ids = [int(s.strip()) for s in site_ids.split(",") if s.strip()]

    hashed = None
    if password:
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    ok = get_db().update_user(
        user_id=user_id,
        username=username,
        role_name=role_name,
        hashed_password=hashed,
        full_name=full_name or None,
        note=note or None,
        site_ids=parsed_site_ids,
    )
    if not ok:
        logger.warning("update_user failed for user_id=%s", user_id)
        return RedirectResponse(url=f"/admin/users?error={urllib.parse.quote_plus('Không thể cập nhật người dùng')}", status_code=303)
    return RedirectResponse(url="/admin/users", status_code=303)


from .kyc_router import invalidate_face_cache

@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    user: dict = Depends(require_menu("users")),
):
    # 1. Delete face directory and trigger AI reload before DB deletion
    identity = str(user_id)
    person_dir = os.path.join(cfg.KNOWN_FACES_DIR, identity)
    if os.path.isdir(person_dir):
        shutil.rmtree(person_dir)
        logger.info("Deleted face directory for user %s", user_id)

    # 2. Invalidate caches
    invalidate_face_cache()
    reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
    with open(reload_trigger, "w") as f:
        f.write(str(time.time()))

    # 3. DB deletion
    ok = get_db().delete_user(user_id)
    if not ok:
        logger.warning("delete_user failed for user_id=%s", user_id)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/{user_id}/face")
async def upload_face(
    user_id: int,
    request: Request,
    face_image: UploadFile = File(...),
    angle: str = Form("front"),
    user: dict = Depends(require_menu("users")),
):
    """Upload a face image for a specific angle and trigger model reload."""
    from fastapi.responses import JSONResponse

    db = get_db()
    target_user = db.get_user_by_id(user_id)
    if not target_user:
        if "application/json" in request.headers.get("accept", "") or request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)
        return RedirectResponse(url="/admin/users?error=user_not_found", status_code=303)

    # Validate angle
    if angle not in ["front", "left", "right", "up", "down"]:
        angle = "front"

    # Save by user_id for consistency
    identity = str(user_id)
    person_dir = os.path.join(cfg.KNOWN_FACES_DIR, identity)
    os.makedirs(person_dir, exist_ok=True)

    file_path = os.path.join(person_dir, f"{angle}.jpg")
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(face_image.file, buffer)

    # Invalidate duplicate check cache
    invalidate_face_cache()

    # Create reload trigger file for InferenceWorker
    reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
    with open(reload_trigger, "w") as f:
        f.write(str(time.time()))

    logger.info("Face uploaded for user %s (%s), angle=%s. Reload triggered.", user_id, identity, angle)
    
    # Return JSON for AJAX, redirect for form submissions
    return JSONResponse({"ok": True, "angle": angle, "identity": identity})
