import logging
import shutil
import os
import tempfile
import cv2
import numpy as np
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from typing import Optional
from frontend.deps import get_menu_context, require_menu, get_current_user, templates
from backend.services.database import db
from backend.services import config as cfg
from backend.ai.kyc_manager import KYCManager
from backend.util.image_utils import decode_image
from backend.repositories.user_repo import get_all_users, get_user_by_id
from frontend.routers.strangers_router import get_stranger_log, update_stranger_status

logger = logging.getLogger(__name__)
_MAX_KYC_IMAGE_BYTES = 10 * 1024 * 1024
FaceAnalysis = None

_face_app = None
_cached_matrix = None
_cached_names = []
_last_reload = 0

def get_face_app():
    from backend.ai.face_service import FaceAnalysisService
    return FaceAnalysisService.get_instance(det_size=(320, 320))

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
                img = decode_image(fpath)
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
                norm = np.linalg.norm(avg_emb)
                if not np.isfinite(norm) or norm <= 1e-6:
                    logger.error("Stored KYC face embeddings cancel out for user_id=%s", person)
                    raise ValueError(f"Stored face embeddings are inconsistent for user {person}")
                avg_emb = avg_emb / norm
                all_embs.append(avg_emb)
                all_names.append(person)
                logger.info("Indexed KYC face for user_id=%s (%s images)", person, len(files_scanned))
            
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


async def _read_upload_image(file: UploadFile) -> bytes:
    return await file.read(_MAX_KYC_IMAGE_BYTES + 1)


def _canonical_jpeg(contents: bytes, kind: str, user_id: int) -> tuple[np.ndarray, bytes] | None:
    if not contents or len(contents) > _MAX_KYC_IMAGE_BYTES:
        logger.warning("KYC %s image is empty or oversized for user_id=%s", kind, user_id)
        return None
    image, dbg = decode_image(contents, return_debug=True)
    if image is None:
        logger.warning("KYC %s image could not be decoded for user_id=%s: %s", kind, user_id, dbg.get("error"))
        return None
    encoded_ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 96])
    if not encoded_ok:
        logger.error("KYC %s JPEG encoding failed for user_id=%s", kind, user_id)
        raise RuntimeError("KYC JPEG encoding failed")
    jpeg_bytes = encoded.tobytes()
    canonical = decode_image(jpeg_bytes)
    if canonical is None:
        logger.error("KYC %s canonical JPEG decode failed for user_id=%s", kind, user_id)
        raise RuntimeError("KYC canonical JPEG decode failed")
    return canonical, jpeg_bytes


def _face_embedding(image: np.ndarray, app: object, kind: str, user_id: int):
    faces = app.get(image)
    if not faces:
        logger.warning("KYC %s image has no detectable face for user_id=%s", kind, user_id)
        return None
    face = max(
        faces,
        key=lambda item: (item.bbox[2] - item.bbox[0]) * (item.bbox[3] - item.bbox[1]),
    ) if len(faces) > 1 else faces[0]
    embedding = np.asarray(face.normed_embedding, dtype=np.float32)
    if embedding.shape != (512,) or not np.isfinite(embedding).all() or np.linalg.norm(embedding) <= 1e-6:
        logger.warning("KYC %s image has an invalid face embedding for user_id=%s", kind, user_id)
        return None
    return faces, embedding


def _write_jpeg_atomic(file_path: str, jpeg_bytes: bytes) -> None:
    directory = os.path.dirname(file_path)
    os.makedirs(directory, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".kyc-upload-", dir=directory) as temporary_dir:
        temporary_file = os.path.join(temporary_dir, "image.jpg")
        with open(temporary_file, "wb") as buffer:
            buffer.write(jpeg_bytes)
        os.replace(temporary_file, file_path)


def _unlink_kyc_file(file_path: str) -> None:
    os.unlink(file_path)


def _read_stranger_snapshot(source_path: str) -> bytes:
    with open(source_path, "rb") as source:
        return source.read(_MAX_KYC_IMAGE_BYTES + 1)

@router.post("/clean-cache")
async def clean_cache(user: dict = Depends(get_current_user)):
    if user.get("role", "").lower() not in ("admin", "manager"):
        return _kyc_error("forbidden", 403)
    
    cfg.trigger_ai_reload()
    
    invalidate_face_cache()
    
    logger.info("Manual AI cache cleanup triggered by user %s", user.get("id"))
    return JSONResponse({"ok": True, "message": "Đã làm sạch bộ nhớ AI và kích hoạt quét lại."})


KYC_UPLOAD_DIR = os.path.join(os.path.dirname(cfg.SNAPSHOT_DIR), "kyc_uploads")



@router.get("")
async def kyc_page(request: Request, user: dict = Depends(get_current_user)):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "kyc"
    kyc = KYCManager()
    uid = user.get("id") or user.get("user_id")
    kyc_detail = kyc.get_user_detail(uid) if uid else None
    kyc_docs = kyc.get_documents(uid) if uid else []
    docs_map = {d["doc_type"]: d for d in kyc_docs}
    ctx["kyc"] = kyc_detail
    ctx["docs"] = docs_map
    return templates.TemplateResponse("pages/kyc.html", {"request": request, **ctx})


@router.post("/basic-info")
async def submit_basic_info(
    user: dict = Depends(get_current_user),
    full_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(...),
    date_of_birth: str = Form(...),
    id_number: str = Form(...),
):
    kyc = KYCManager()
    kyc.update_basic_info(user["id"], full_name, phone, email, date_of_birth, id_number)
    logger.info("KYC Level 1 submitted for user %s", user["id"])
    return RedirectResponse(url="/kyc", status_code=303)


@router.post("/upload-id")
async def upload_id_doc(
    user: dict = Depends(get_current_user),
    doc_type: str = Form(...),
    file: UploadFile = File(...),
):
    if doc_type not in ("id_front", "id_back", "selfie_with_id"):
        return _kyc_error("invalid_doc_type", 400)

    contents = await _read_upload_image(file)
    canonical = _canonical_jpeg(contents, doc_type, user["id"])
    if canonical is None:
        return _kyc_error("invalid_image", 400)

    user_dir = os.path.join(KYC_UPLOAD_DIR, str(user["id"]))
    file_path = os.path.join(user_dir, f"{doc_type}.jpg")
    _write_jpeg_atomic(file_path, canonical[1])

    kyc = KYCManager()
    if not kyc.insert_document(user["id"], doc_type, file_path):
        return _kyc_error("save_failed", 500)

    docs = kyc.get_documents(user["id"])
    doc_types = {d["doc_type"] for d in docs}
    if {"id_front", "id_back"}.issubset(doc_types):
        kyc_detail = kyc.get_user_detail(user["id"])
        if kyc_detail and kyc_detail["kyc_level"] < 2:
            kyc.update_level(user["id"], 2)

    logger.info("KYC doc '%s' uploaded for user %s", doc_type, user["id"])
    return JSONResponse({"ok": True, "doc_type": doc_type})


@router.post("/delete-id")
async def delete_id_doc(
    user: dict = Depends(get_current_user),
    doc_type: str = Form(...),
):
    if doc_type not in ("id_front", "id_back", "selfie_with_id"):
        return _kyc_error("invalid_doc_type", 400)

    user_dir = os.path.join(KYC_UPLOAD_DIR, str(user["id"]))
    file_path = os.path.join(user_dir, f"{doc_type}.jpg")
    if os.path.exists(file_path):
        _unlink_kyc_file(file_path)

    kyc = KYCManager()
    if not kyc.delete_document(user["id"], doc_type):
        return _kyc_error("document_not_found", 404)
    logger.info("KYC doc '%s' deleted for user %s", doc_type, user["id"])
    return JSONResponse({"ok": True, "doc_type": doc_type})


@router.post("/upload-face")
async def upload_face(
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

    contents = await _read_upload_image(file)
    canonical = _canonical_jpeg(contents, f"face_{angle}", target_id)
    if canonical is None:
        return _kyc_error("invalid_image", 400)
    img_cv, jpeg_bytes = canonical

    app = get_face_app()
    verified_face = _face_embedding(img_cv, app, f"face_{angle}", target_id)
    if verified_face is None:
        return _kyc_error("no_face_detected", 400)
    _, new_emb = verified_face

    cached_names, cached_matrix = get_face_matrix(app)
    if cached_matrix is not None and len(cached_matrix) > 0:
        similarity = np.dot(cached_matrix, new_emb)
        idx = np.argmax(similarity)
        max_val = similarity[idx]
        dup_target = cached_names[idx]
            
        logger.info("KYC duplicate check for user_id=%s: max_similarity=%.4f with user_id=%s",
                    target_id, max_val, dup_target)
            
        if max_val > 0.45:
            if str(dup_target) != str(target_id):
                dup_user = get_user_by_id(int(dup_target)) if dup_target.isdigit() else None
                dup_username = dup_user["username"] if dup_user else dup_target
                    
                logger.warning("KYC duplicate face rejected for user_id=%s matched_user_id=%s similarity=%.4f",
                                target_id, dup_target, max_val)
                return JSONResponse({
                    "ok": False,
                    "error": "duplicate_face",
                    "message": f"Khuôn mặt này đã được cấu hình cho tài khoản: {dup_username}"
                }, status_code=400)

    identity = str(target_id)
    person_dir = os.path.join(cfg.KNOWN_FACES_DIR, identity)
    face_path = os.path.join(person_dir, f"{angle}.jpg")
    _write_jpeg_atomic(face_path, jpeg_bytes)
    
    invalidate_face_cache()

    kyc = KYCManager()
    if not kyc.insert_document(target_id, f"face_{angle}", face_path):
        return _kyc_error("save_failed", 500)
    cfg.trigger_ai_reload()

    docs = kyc.get_documents(target_id)
    face_types = {d["doc_type"] for d in docs if d["doc_type"].startswith("face_")}
    required_faces = {f"face_{a}" for a in valid_angles}
    if required_faces.issubset(face_types):
        kyc_detail = kyc.get_user_detail(target_id)
        if kyc_detail and kyc_detail["kyc_level"] < 4:
            kyc.update_level(target_id, 4)

    logger.info("KYC face '%s' uploaded and indexed for user %s", angle, target_id)
    return JSONResponse({"ok": True, "angle": angle})


@router.post("/face/{angle}/delete")
async def delete_face(
    angle: str,
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
        _unlink_kyc_file(face_path)
        
        if os.path.exists(person_dir):
            has_images = any(f.lower().endswith(('.jpg', '.jpeg', '.png')) for f in os.listdir(person_dir))
            if not has_images:
                shutil.rmtree(person_dir)
                logger.info("Removed empty face directory for user %s", target_id)
    
    invalidate_face_cache()
        
    cfg.trigger_ai_reload()

    kyc = KYCManager()
    if not kyc.delete_document(target_id, f"face_{angle}"):
        return _kyc_error("document_not_found", 404)

    docs = kyc.get_documents(target_id)
    face_types = {d["doc_type"] for d in docs if d["doc_type"].startswith("face_")}
    required_faces = {"face_front", "face_left", "face_right", "face_up", "face_down"}
    if not required_faces.issubset(face_types):
        kyc_detail = kyc.get_user_detail(target_id)
        if kyc_detail and kyc_detail["kyc_level"] >= 3:
            kyc.update_level(target_id, 2)
            
    return JSONResponse({"ok": True})


@router.post("/reset")
async def reset_kyc(user: dict = Depends(get_current_user)):
    kyc = KYCManager()
    kyc_detail = kyc.get_user_detail(user["id"])
    if not kyc_detail:
        return _kyc_error("user_not_found", 404)
    if kyc_detail["kyc_level"] != -1:
        return _kyc_error("invalid_state", 409)
    kyc.reset(user["id"])
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
async def approve_kyc(user_id: int, user: dict = Depends(require_menu("users"))):
    kyc = KYCManager()
    if not kyc.approve(user_id, user["username"]):
        return _kyc_error("user_not_found", 404)
    logger.info("KYC approved for user_id=%s by reviewer_id=%s", user_id, user.get("id"))
    return RedirectResponse(url="/kyc/admin", status_code=303)


@router.post("/admin/{user_id}/reject")
async def reject_kyc(
    user_id: int,
    user: dict = Depends(require_menu("users")),
    reason: str = Form("Không đạt yêu cầu"),
):
    kyc = KYCManager()
    if not kyc.reject(user_id, user["username"], reason):
        return _kyc_error("user_not_found", 404)
    logger.info("KYC rejected for user_id=%s by reviewer_id=%s", user_id, user.get("id"))
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
        logger.error("KYC stranger snapshot missing for stranger_id=%s", stranger_id)
        return _kyc_error("snapshot_file_missing", 404)

    direction = stranger.get("direction") or "frontal"
    angle = "front" if direction == "frontal" else direction
    if angle not in ("front", "left", "right", "up", "down"):
        return _kyc_error("invalid_angle", 422)
        
    contents = _read_stranger_snapshot(source_path)
    canonical = _canonical_jpeg(contents, f"stranger_snapshot_{stranger_id}", user_id)
    if canonical is None:
        return _kyc_error("invalid_image", 400)
    if _face_embedding(canonical[0], get_face_app(), "stranger_snapshot", user_id) is None:
        return _kyc_error("no_face_detected", 400)

    target_path = os.path.join(cfg.KNOWN_FACES_DIR, str(user_id), f"{angle}.jpg")
    _write_jpeg_atomic(target_path, canonical[1])
    logger.info("Imported stranger snapshot stranger_id=%s angle=%s for user_id=%s", stranger_id, angle, user_id)
    
    kyc = KYCManager()
    if not kyc.insert_document(user_id, f"face_{angle}", target_path):
        return _kyc_error("save_failed", 500)
    
    kyc.update_level(user_id, 4)

    if not update_stranger_status(stranger_id, "converted", converted_user_id=user_id, note="Registered from webcam/RTSP"):
        return _kyc_error("stranger_not_found", 404)

    from backend.ai.stranger_manager import get_stranger_manager
    get_stranger_manager().remove(stranger_id)
        
    invalidate_face_cache()
    
    cfg.trigger_ai_reload()
        
    return RedirectResponse(url="/strangers", status_code=303)
