import logging
import shutil
import os
import time
import cv2
import numpy as np
from insightface.app import FaceAnalysis
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from typing import Optional
from frontend.deps import get_menu_context, require_menu, get_current_user, templates
from backend.services.database import db
from backend.services import config as cfg
from backend.ai.kyc_manager import KYCManager
from frontend.routers.users_router import get_all_users, get_user_by_id
from frontend.routers.strangers_router import get_stranger_log, update_stranger_status

logger = logging.getLogger(__name__)

_face_app = None
_cached_matrix = None
_cached_names = []
_last_reload = 0

def get_face_app():
    global _face_app
    if _face_app is None:
        logger.info("Initializing InsightFace in KYC router for duplicate checks...")
        _face_app = FaceAnalysis(name="buffalo_sc", root=cfg.FACE_MODEL_DIR, providers=["CPUExecutionProvider"])
        _face_app.prepare(ctx_id=-1, det_size=(320, 320))
    return _face_app

def invalidate_face_cache():
    global _cached_matrix, _cached_names, _last_reload
    logger.info("Invalidating KYC face matrix cache.")
    _cached_matrix = None
    _cached_names = []
    _last_reload = 0

def get_face_matrix(app):
    global _cached_matrix, _cached_names, _last_reload
    reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
    
    mtime = os.path.getmtime(reload_trigger) if os.path.exists(reload_trigger) else 0
    
    if _cached_matrix is not None and _last_reload >= mtime and mtime > 0:
        return _cached_names, _cached_matrix

    logger.info("Building face matrix cache for duplicate check...")
    all_embs = []
    all_names = []
    
    if os.path.isdir(cfg.KNOWN_FACES_DIR):
        for person in sorted(os.listdir(cfg.KNOWN_FACES_DIR)):
            if person.startswith("."): continue
            if not person.isdigit(): continue
            person_dir = os.path.join(cfg.KNOWN_FACES_DIR, person)
            if not os.path.isdir(person_dir): continue
            
            embeddings = []
            files_scanned = []
            for fname in os.listdir(person_dir):
                if not fname.lower().endswith((".jpg", ".jpeg", ".png")): continue
                fpath = os.path.join(person_dir, fname)
                img = cv2.imread(fpath)
                if img is None:
                    logger.error("Stored KYC face image is unreadable for user_id=%s", person)
                    raise ValueError(f"Stored face image is unreadable for user {person}")
                faces = app.get(img)
                if not faces:
                    logger.error("Stored KYC face image has no face for user_id=%s", person)
                    raise ValueError(f"Stored face image has no detectable face for user {person}")
                embedding = np.asarray(faces[0].normed_embedding, dtype=np.float32)
                if embedding.shape != (512,) or not np.isfinite(embedding).all() or np.linalg.norm(embedding) <= 1e-6:
                    logger.error("Stored KYC face image has an invalid embedding for user_id=%s", person)
                    raise ValueError(f"Stored face image has an invalid embedding for user {person}")
                embeddings.append(embedding)
                files_scanned.append(fname)
            
            if embeddings:
                avg_emb = np.mean(embeddings, axis=0)
                avg_emb = avg_emb / np.linalg.norm(avg_emb)
                all_embs.append(avg_emb)
                all_names.append(person)
                logger.info(f"  + Indexed user {person} (Files: {', '.join(files_scanned)})")
            
    if all_embs:
        _cached_matrix = np.array(all_embs)
        _cached_names = all_names
    else:
        _cached_matrix = np.array([])
        _cached_names = []
    
    logger.info(f"Face matrix rebuild complete. Total indexed users: {len(_cached_names)} ({', '.join(_cached_names)})")
    
    _last_reload = mtime
    return _cached_names, _cached_matrix


router = APIRouter(prefix="/kyc")

_KYC_ERROR_MESSAGES = {
    "forbidden": "Bạn không có quyền thực hiện thao tác này.",
    "invalid_doc_type": "Loại giấy tờ không hợp lệ.",
    "invalid_angle": "Góc chụp không hợp lệ.",
    "invalid_image": "Ảnh không hợp lệ. Vui lòng chụp lại.",
    "no_face_detected": "Không phát hiện khuôn mặt trong ảnh.",
    "Disk write failed": "Không thể lưu ảnh. Vui lòng thử lại.",
    "stranger_not_found": "Không tìm thấy người lạ.",
    "user_not_found": "Không tìm thấy người dùng.",
    "snapshot_file_missing": "Không tìm thấy ảnh chụp nguồn.",
    "document_not_found": "Không tìm thấy giấy tờ cần xóa.",
    "save_failed": "Không thể lưu thông tin xác minh. Vui lòng thử lại.",
    "invalid_state": "Trạng thái xác minh đã thay đổi. Vui lòng tải lại trang.",
}


def _kyc_error(code: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": code, "message": _KYC_ERROR_MESSAGES[code]},
        status_code=status_code,
    )

@router.post("/clean-cache")
async def clean_cache(user: dict = Depends(get_current_user)):
    if user.get("role", "").lower() not in ("admin", "manager"):
        return _kyc_error("forbidden", 403)
    
    reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
    with open(reload_trigger, "w") as f:
        f.write(str(time.time()))
    
    invalidate_face_cache()
    
    logger.info("Manual AI cache cleanup triggered by user %s", user.get("id"))
    return JSONResponse({"ok": True, "message": "Đã làm sạch bộ nhớ AI và kích hoạt quét lại."})


KYC_UPLOAD_DIR = os.path.join(os.path.dirname(cfg.SNAPSHOT_DIR), "kyc_uploads")
os.makedirs(KYC_UPLOAD_DIR, exist_ok=True)



@router.get("")
async def kyc_page(request: Request, user: dict = Depends(get_current_user)):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "kyc"
    kyc = KYCManager()
    kyc_detail = kyc.get_user_detail(user["id"])
    kyc_docs = kyc.get_documents(user["id"])
    docs_map = {d["doc_type"]: d for d in kyc_docs}
    ctx["kyc"] = kyc_detail
    ctx["docs"] = docs_map
    return templates.TemplateResponse("pages/kyc.html", {"request": request, **ctx})


@router.post("/basic-info")
async def submit_basic_info(
    request: Request,
    user: dict = Depends(get_current_user),
    full_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(...),
    date_of_birth: str = Form(...),
    id_number: str = Form(...),
):
    kyc = KYCManager()
    if not kyc.update_basic_info(user["id"], full_name, phone, email, date_of_birth, id_number):
        return _kyc_error("user_not_found", 404)
    logger.info("KYC Level 1 submitted for user %s", user["id"])
    return RedirectResponse(url="/kyc", status_code=303)


@router.post("/upload-id")
async def upload_id_doc(
    request: Request,
    user: dict = Depends(get_current_user),
    doc_type: str = Form(...),
    file: UploadFile = File(...),
):
    if doc_type not in ("id_front", "id_back", "selfie_with_id"):
        return _kyc_error("invalid_doc_type", 400)

    user_dir = os.path.join(KYC_UPLOAD_DIR, str(user["id"]))
    os.makedirs(user_dir, exist_ok=True)

    file_path = os.path.join(user_dir, f"{doc_type}.jpg")
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    kyc = KYCManager()
    if not kyc.insert_document(user["id"], doc_type, file_path):
        logger.error("KYC document could not be recorded for user %s", user["id"])
        return _kyc_error("save_failed", 500)

    docs = kyc.get_documents(user["id"])
    doc_types = {d["doc_type"] for d in docs}
    if {"id_front", "id_back"}.issubset(doc_types):
        kyc_detail = kyc.get_user_detail(user["id"])
        if kyc_detail and kyc_detail["kyc_level"] < 2:
            if not kyc.update_level(user["id"], 2):
                return _kyc_error("user_not_found", 404)

    logger.info("KYC doc '%s' uploaded for user %s", doc_type, user["id"])
    return JSONResponse({"ok": True, "doc_type": doc_type})


@router.post("/delete-id")
async def delete_id_doc(
    request: Request,
    user: dict = Depends(get_current_user),
    doc_type: str = Form(...),
):
    if doc_type not in ("id_front", "id_back", "selfie_with_id"):
        return _kyc_error("invalid_doc_type", 400)

    user_dir = os.path.join(KYC_UPLOAD_DIR, str(user["id"]))
    file_path = os.path.join(user_dir, f"{doc_type}.jpg")
    if os.path.exists(file_path):
        os.remove(file_path)

    kyc = KYCManager()
    if not kyc.delete_document(user["id"], doc_type):
        return _kyc_error("document_not_found", 404)
    logger.info("KYC doc '%s' deleted for user %s", doc_type, user["id"])
    return JSONResponse({"ok": True, "doc_type": doc_type})


@router.post("/upload-face")
async def upload_face(
    request: Request,
    user: dict = Depends(get_current_user),
    angle: str = Form(...),
    file: UploadFile = File(...),
    target_user_id: Optional[int] = Form(None),
):
    valid_angles = ("front", "left", "right", "up", "down")
    if angle not in valid_angles:
        return _kyc_error("invalid_angle", 400)

    target_id = target_user_id if target_user_id else user["id"]
    if target_id != user["id"] and user.get("role", "").lower() not in ("admin", "manager"):
        return _kyc_error("forbidden", 403)

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img_cv is None:
        return _kyc_error("invalid_image", 400)

    app = get_face_app()
    if app:
        faces = app.get(img_cv)
        if not faces:
            logger.warning(f"KYC rejection: no_face_detected for user={target_id}")
            return _kyc_error("no_face_detected", 400)
            
        faces = sorted(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]), reverse=True)
        new_emb = faces[0].normed_embedding

        cached_names, cached_matrix = get_face_matrix(app)
        if cached_matrix is not None and len(cached_matrix) > 0:
            similarity = np.dot(cached_matrix, new_emb)
            idx = np.argmax(similarity)
            max_val = similarity[idx]
            dup_target = cached_names[idx]
            
            logger.info(f"Duplicate check for user {target_id}: max_similarity={max_val:.4f} with user {dup_target}")
            
            if max_val > 0.45:
                if str(dup_target) != str(target_id):
                    dup_user = get_user_by_id(int(dup_target)) if dup_target.isdigit() else None
                    dup_username = dup_user["username"] if dup_user else dup_target
                    
                    logger.warning(f"KYC rejection: duplicate_face for user={target_id}. Matched {dup_target} ({dup_username}) at {max_val:.4f}")
                    return JSONResponse({
                        "ok": False, 
                        "error": "duplicate_face",
                        "message": f"Khuôn mặt này đã được cấu hình cho tài khoản: {dup_username}"
                    }, status_code=400)

    identity = str(target_id)
    person_dir = os.path.join(cfg.KNOWN_FACES_DIR, identity)
    os.makedirs(person_dir, exist_ok=True)
    face_path = os.path.join(person_dir, f"{angle}.jpg")
    
    success = cv2.imwrite(face_path, img_cv)
    if not success:
        logger.error(f"Failed to write image to disk (possibly disk full): {face_path}")
        return _kyc_error("Disk write failed", 500)
    
    invalidate_face_cache()

    kyc = KYCManager()
    if not kyc.insert_document(target_id, f"face_{angle}", face_path):
        logger.error("KYC face document could not be recorded for user %s", target_id)
        return _kyc_error("save_failed", 500)

    docs = kyc.get_documents(target_id)
    face_types = {d["doc_type"] for d in docs if d["doc_type"].startswith("face_")}
    required_faces = {f"face_{a}" for a in valid_angles}
    if required_faces.issubset(face_types):
        kyc_detail = kyc.get_user_detail(target_id)
        if kyc_detail and kyc_detail["kyc_level"] < 4:
            if not kyc.update_level(target_id, 4):
                return _kyc_error("user_not_found", 404)
            reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
            with open(reload_trigger, "w") as f:
                f.write(str(time.time()))

    logger.info("KYC face '%s' uploaded and indexed for user %s", angle, target_id)
    return JSONResponse({"ok": True, "angle": angle})


@router.post("/face/{angle}/delete")
async def delete_face(
    angle: str,
    request: Request,
    user: dict = Depends(get_current_user),
    target_user_id: Optional[int] = None,
):
    valid_angles = ("front", "left", "right", "up", "down")
    if angle not in valid_angles:
        return _kyc_error("invalid_angle", 400)
    
    target_id = target_user_id if target_user_id else user["id"]
    if target_id != user["id"] and user.get("role", "").lower() not in ("admin", "manager"):
        return _kyc_error("forbidden", 403)

    person_dir = os.path.join(cfg.KNOWN_FACES_DIR, str(target_id))
    face_path = os.path.join(person_dir, f"{angle}.jpg")
    
    if os.path.exists(face_path):
        os.unlink(face_path)
        
        if os.path.exists(person_dir):
            has_images = any(f.lower().endswith(('.jpg', '.jpeg', '.png')) for f in os.listdir(person_dir))
            if not has_images:
                import shutil
                shutil.rmtree(person_dir)
                logger.info("Removed empty face directory for user %s", target_id)
    
    invalidate_face_cache()
        
    reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
    with open(reload_trigger, "w") as f:
        f.write(str(time.time()))

    kyc = KYCManager()
    if not kyc.delete_document(target_id, f"face_{angle}"):
        return _kyc_error("document_not_found", 404)

    docs = kyc.get_documents(target_id)
    face_types = {d["doc_type"] for d in docs if d["doc_type"].startswith("face_")}
    required_faces = {"face_front", "face_left", "face_right", "face_up", "face_down"}
    if not required_faces.issubset(face_types):
        kyc_detail = kyc.get_user_detail(target_id)
        if kyc_detail and kyc_detail["kyc_level"] >= 3:
            if not kyc.update_level(target_id, 2):
                return _kyc_error("user_not_found", 404)
            
    return JSONResponse({"ok": True})


@router.post("/reset")
async def reset_kyc(request: Request, user: dict = Depends(get_current_user)):
    kyc = KYCManager()
    kyc_detail = kyc.get_user_detail(user["id"])
    if not kyc_detail:
        return _kyc_error("user_not_found", 404)
    if kyc_detail["kyc_level"] != -1:
        return _kyc_error("invalid_state", 409)
    if not kyc.reset(user["id"]):
        return _kyc_error("user_not_found", 404)
    logger.info("KYC reset for user %s", user["id"])
    return RedirectResponse(url="/kyc", status_code=303)



@router.get("/admin")
async def kyc_admin_page(request: Request, user: dict = Depends(require_menu("users"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "kyc_admin"
    kyc = KYCManager()
    ctx["pending_users"] = kyc.get_pending_users()
    all_users = get_all_users()
    ctx["all_kyc_users"] = [u for u in all_users if u.get("kyc_level", 0) != 0] if all_users else []
    return templates.TemplateResponse("pages/kyc_admin.html", {"request": request, **ctx})


@router.get("/admin/{user_id}")
async def kyc_admin_detail(user_id: int, request: Request, user: dict = Depends(require_menu("users"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "kyc_admin"
    kyc = KYCManager()
    ctx["target"] = kyc.get_user_detail(user_id)
    ctx["docs"] = kyc.get_documents(user_id)
    return templates.TemplateResponse("pages/kyc_admin_detail.html", {"request": request, **ctx})


@router.post("/admin/{user_id}/approve")
async def approve_kyc(user_id: int, request: Request, user: dict = Depends(require_menu("users"))):
    kyc = KYCManager()
    if not kyc.approve(user_id, user["username"]):
        return _kyc_error("user_not_found", 404)
    logger.info("KYC approved for user %s by %s", user_id, user["username"])
    return RedirectResponse(url="/kyc/admin", status_code=303)


@router.post("/admin/{user_id}/reject")
async def reject_kyc(
    user_id: int,
    request: Request,
    user: dict = Depends(require_menu("users")),
    reason: str = Form("Không đạt yêu cầu"),
):
    kyc = KYCManager()
    if not kyc.reject(user_id, user["username"], reason):
        return _kyc_error("user_not_found", 404)
    logger.info("KYC rejected for user %s by %s", user_id, user["username"])
    return RedirectResponse(url="/kyc/admin", status_code=303)



@router.get("/uploads/{user_id}/{filename}")
async def serve_kyc_upload(user_id: int, filename: str, user: dict = Depends(get_current_user)):
    from fastapi.responses import FileResponse
    if user.get("id") != user_id and user.get("role") not in ("Admin", "Manager"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "bad_request"}, status_code=400)
    base = os.path.realpath(os.path.join(KYC_UPLOAD_DIR, str(user_id)))
    file_path = os.path.realpath(os.path.join(base, filename))
    if file_path != base and not file_path.startswith(base + os.sep):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    return JSONResponse({"error": "not_found"}, status_code=404)

@router.post("/register-stranger")
async def register_stranger(
    request: Request,
    user: dict = Depends(require_menu("strangers")),
    stranger_id: int = Form(...),
    user_id: int = Form(...),
):
    stranger = get_stranger_log(stranger_id)
    if not stranger:
        return _kyc_error("stranger_not_found", 404)
        
    target_user = get_user_by_id(user_id)
    if not target_user:
        return _kyc_error("user_not_found", 404)
        
    source_path = stranger["image_path"]
    if not source_path or not os.path.exists(source_path):
        return _kyc_error("snapshot_file_missing", 404)

    direction = stranger.get("direction") or "frontal"
    angle = "front" if direction == "frontal" else direction
    if angle not in ("front", "left", "right", "up", "down"):
        return _kyc_error("invalid_angle", 422)
        
    identity = str(user_id)
    person_dir = os.path.join(cfg.KNOWN_FACES_DIR, identity)
    os.makedirs(person_dir, exist_ok=True)
    
    filename = f"{angle}.jpg"
    target_path = os.path.join(person_dir, filename)
    
    shutil.copy2(source_path, target_path)
    logger.info("Copied stranger snapshot %s (%s) to user %s face directory.", stranger_id, direction, user_id)
    
    kyc = KYCManager()
    if not kyc.insert_document(user_id, f"face_{angle}", target_path):
        logger.error("KYC stranger face could not be recorded for user %s", user_id)
        return _kyc_error("save_failed", 500)
    
    kyc_detail = kyc.get_user_detail(user_id)
    if kyc_detail:
        if not kyc.update_level(user_id, 4):
            return _kyc_error("user_not_found", 404)
    else:
        return _kyc_error("user_not_found", 404)

    if not update_stranger_status(stranger_id, "converted", converted_user_id=user_id, note="Registered from webcam/RTSP"):
        return _kyc_error("stranger_not_found", 404)

    from backend.ai.stranger_manager import get_stranger_manager
    get_stranger_manager().remove(stranger_id)
        
    invalidate_face_cache()
    
    reload_trigger = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
    with open(reload_trigger, "w") as f:
        f.write(str(time.time()))
        
    return RedirectResponse(url="/strangers", status_code=303)
