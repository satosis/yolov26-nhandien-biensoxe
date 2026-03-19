import time
import re
import cv2
import numpy as np
import os
import threading
import logging
from core.database import DatabaseManager
from core.config import normalize_plate
from util.drawing_utils import draw_premium_label

logger = logging.getLogger(__name__)

# Whitelist cache (avoid DB query per frame)
_whitelist_cache = {"data": set(), "ts": 0}
_WHITELIST_TTL = 30  # refresh every 30 seconds

# Pending plate cooldown (avoid spamming DB with same unknown plate)
_pending_plate_seen = {}  # plate_norm -> last_seen_time
_PENDING_COOLDOWN = 60  # seconds

# OCR active learning cooldown
_ocr_sample_seen = {}
_OCR_SAMPLE_COOLDOWN = 120  # seconds

def _is_plate_whitelisted(plate_text: str) -> bool:
    """Check if plate is in whitelist, with 30s cache."""
    now = time.time()
    if now - _whitelist_cache["ts"] > _WHITELIST_TTL:
        try:
            db = DatabaseManager()
            rows = db.select("plate_whitelist", columns=["plate_number"], conditions={"is_active": True})
            _whitelist_cache["data"] = {normalize_plate(r['plate_number']) for r in rows}
        except Exception as e:
            logger.warning("Whitelist cache refresh failed: %s", e)
        _whitelist_cache["ts"] = now
    return normalize_plate(plate_text) in _whitelist_cache["data"]


def _handle_unknown_plate(plate_text: str, ocr_conf: float, plate_crop, camera_id: str = None):
    """Log unknown plate to pending_plates + save OCR sample if low confidence."""
    now = time.time()
    plate_norm = normalize_plate(plate_text)

    # 1. Add to pending plates (cooldown 60s per plate)
    if not _is_plate_whitelisted(plate_text):
        last = _pending_plate_seen.get(plate_norm, 0)
        if now - last > _PENDING_COOLDOWN:
            _pending_plate_seen[plate_norm] = now
            try:
                db = DatabaseManager()
                db.add_pending_plate_simple(plate_norm, plate_text, camera_id)
            except Exception as e:
                logger.warning("Failed to add pending plate %s: %s", plate_norm, e)

    # 2. OCR active learning: save low-confidence samples (0.3-0.7)
    if 0.3 <= ocr_conf <= 0.7 and plate_crop is not None and plate_crop.size > 0:
        last_sample = _ocr_sample_seen.get(plate_norm, 0)
        if now - last_sample > _OCR_SAMPLE_COOLDOWN:
            _ocr_sample_seen[plate_norm] = now
            try:
                import cv2
                sample_dir = "data/snapshots/ocr_samples"
                os.makedirs(sample_dir, exist_ok=True)
                fname = f"ocr_{plate_norm}_{int(now)}.jpg"
                fpath = os.path.join(sample_dir, fname)
                cv2.imwrite(fpath, plate_crop)
                db = DatabaseManager()
                db.save_ocr_training_sample(fpath, plate_text, ocr_conf, camera_id)
            except Exception as e:
                logger.warning("Failed to save OCR sample %s: %s", plate_norm, e)

# ── COCO class IDs we care about ──
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck", 1: "bicycle"}
PERSON_CLASS = {0: "person"}

# Custom model classes (when using custom_detector.pt)
CUSTOM_VEHICLE_CLASSES = {1: "car", 2: "truck", 3: "motorcycle"}
CUSTOM_PERSON_CLASS = {0: "person"}
CUSTOM_PLATE_CLASS = {4: "license_plate"}
CUSTOM_DOOR_CLASSES = {5: "door_open", 6: "door_closed"}

# Class names that indicate a vehicle (for flexible matching)
VEHICLE_NAMES = {"car", "truck", "motorcycle", "bus", "bicycle", "vehicle"}
PERSON_NAMES = {"person", "human", "pedestrian", "face"}
PLATE_NAMES = {"license_plate", "plate", "license plate", "number_plate"}
DOOR_NAMES = {"door_open", "door_closed", "gate_open", "gate_closed"}

# Vietnamese plate pattern
VN_PLATE_PATTERN = re.compile(r'\d{2}[A-Z]\d{3,5}\.?\d{0,2}')


def enhance_plate(crop: np.ndarray) -> np.ndarray:
    """Enhance a cropped image for better OCR readability."""
    h, w = crop.shape[:2]
    # Scale up for better OCR
    scale = max(3, min(6, 300 // max(h, 1)))
    up = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    # CLAHE for better contrast
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    blur = cv2.GaussianBlur(enhanced, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def read_plate_ocr(ocr, crop: np.ndarray) -> tuple[str, float]:
    """Run PaddleOCR on a crop and extract plate text with spatial sorting."""
    if ocr is None:
        return "", 0.0
    try:
        result = ocr.ocr(crop, cls=True)
    except Exception:
        return "", 0.0
    
    if not result or not result[0]:
        return "", 0.0
    
    # PaddleOCR result item: [[box], [text, conf]]
    # box is: [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
    items = result[0]
    
    # Spatial sorting for multi-line plates (88C \n 073.04)
    # Sort primarily by center Y (to group lines), then by center X
    def get_centers(item):
        box = item[0]
        cy = sum(p[1] for p in box) / 4.0
        cx = sum(p[0] for p in box) / 4.0
        return cx, cy

    # Group into lines (delta Y < 20% of image height)
    h_img = crop.shape[0]
    threshold = h_img * 0.2
    
    # First sort by Y
    items.sort(key=lambda x: get_centers(x)[1])
    
    lines = []
    if items:
        current_line = [items[0]]
        for i in range(1, len(items)):
            if get_centers(items[i])[1] - get_centers(current_line[-1])[1] < threshold:
                current_line.append(items[i])
            else:
                lines.append(current_line)
                current_line = [items[i]]
        lines.append(current_line)
    
    # Sort each line by X and join
    texts = []
    confs = []
    for line in lines:
        line.sort(key=lambda x: get_centers(x)[0])
        for item in line:
            texts.append(item[1][0])
            confs.append(float(item[1][1]))
            
    if not texts:
        return "", 0.0

    raw_text = " ".join(texts).upper()
    # Clean up: keep only alphanumerics, dots, dashes, and spaces for multi-line display
    cleaned = re.sub(r'[^A-Z0-9.\- ]', '', raw_text).strip()
    # Post-process: fix common OCR errors for Vietnamese plates
    cleaned = _fix_vn_plate_ocr(cleaned)
    avg_conf = sum(confs) / len(confs)
    return cleaned, avg_conf


def _fix_vn_plate_ocr(text: str) -> str:
    """Fix common PaddleOCR misreads for Vietnamese license plates.

    VN plate format: XXY NNNNN or XXY NNN.NN
    XX = 2-digit province code (11-99)
    Y  = 1 letter (A-Z)
    N  = digits

    Common OCR errors: C→0, B→8, D→0, G→6, S→5, O→0, I→1, Z→2
    """
    # Remove all spaces/dots/dashes for analysis
    raw = re.sub(r'[\s.\-]', '', text)
    if len(raw) < 5:
        return text

    # Position 2 (index 2) should be a letter in VN plates
    # OCR often reads it as a digit: 0→C/O/D, 8→B, 6→G, 5→S, 1→I
    digit_to_letter = {'0': 'C', '8': 'B', '6': 'G', '5': 'S', '1': 'I', '2': 'Z'}
    letter_to_digit = {'C': '0', 'O': '0', 'D': '0', 'B': '8', 'G': '6', 'S': '5', 'I': '1', 'Z': '2'}

    chars = list(raw)

    # First 2 chars should be digits (province code)
    for i in range(min(2, len(chars))):
        if chars[i] in letter_to_digit:
            chars[i] = letter_to_digit[chars[i]]

    # 3rd char (index 2) should be a letter
    if len(chars) > 2 and chars[2].isdigit():
        if chars[2] in digit_to_letter:
            chars[2] = digit_to_letter[chars[2]]

    # Remaining chars (index 3+) should be digits
    for i in range(3, len(chars)):
        if chars[i] in letter_to_digit:
            chars[i] = letter_to_digit[chars[i]]

    fixed = ''.join(chars)

    # Re-format: XXY NNNNN or XXY NNN.NN
    if len(fixed) >= 6:
        prefix = fixed[:3]  # e.g. 88C
        suffix = fixed[3:]  # e.g. 07304
        if len(suffix) >= 5:
            return f"{prefix} {suffix[:3]}.{suffix[3:5]}"
        elif len(suffix) >= 3:
            return f"{prefix} {suffix}"
        else:
            return f"{prefix} {suffix}"

    return fixed


# Removed find_plate_region: Redundant and slow on ARM when using YOLO plate detection.


def draw_label(img, x1, y1, x2, y2, label, color):
    """Draw label with premium style."""
    draw_premium_label(img, x1, y1, x2, y2, label, color)


class DetectionController:
    def __init__(self, model, ocr, face_app):
        self.model = model
        self.ocr = ocr
        self.face_app = face_app
        self.db = DatabaseManager()
        self._face_cache = {} # {abs_path: {person_name: embedding}}
        self._cache_info = {} # {abs_path: {"ts": time, "mtime": mtime}}
        self._db_identity_cache = {} # {person_name: {"full_name": ..., "username": ...}}
        # Use shared lock from face_app if available
        self._lock = getattr(face_app, 'lock', threading.Lock())
        self._load_lock = threading.Lock() # Lock purely for cache loading
        self._last_log = "Chưa khởi tạo cache"
        self._seen_strangers = {}  # {cam_key: last_saved_time}
        self._stranger_cooldown = 60  # seconds

    def get_cache_status(self, known_dir=None):
        """Return diagnostic info about the face cache."""
        if known_dir is None:
            known_dir = "./config/faces"
        path = os.path.abspath(known_dir)
        known = self._face_cache.get(path, {})
        return {
            "loaded": len(known),
            "log": self._last_log,
            "identities": list(known.keys())
        }

    def clear_face_cache(self):
        with self._load_lock:
            self._face_cache = {}
            self._cache_info = {}
            self._db_identity_cache = {}
            self._last_log = "Đã xóa bộ nhớ (Cache cleared)."

    def detect_plates(self, img: np.ndarray, config: dict, draw: bool = True):
        """
        2-Stage Detection Pipeline:
        Stage 1: YOLO detects objects. Supports both COCO and Custom (7-class) models.
        Stage 2: OCR fine-tuning for plates.
        """
        conf_thresh = config.get('conf_thresh', 0.25)
        iou_thresh = config.get('iou_thresh', 0.45)
        t0 = time.perf_counter()
        
        # Check if we are using the custom model or COCO
        is_custom = "license_plate" in self.model.names.values()
        
        results = self.model(img, imgsz=640, conf=conf_thresh, iou=iou_thresh, verbose=False)

        detections = []
        annotated = img.copy() if draw else img
        
        vehicles_found = []
        persons_found = []
        doors_found = []
        direct_plates = []

        h_img, w_img = img.shape[:2]
        img_area = h_img * w_img

        for r in results:
            for b in r.boxes:
                cls_id = int(b.cls[0])
                x1, y1, x2, y2 = map(int, b.xyxy[0])
                det_conf = float(b.conf[0])
                
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w_img, x2), min(h_img, y2)
                bw, bh = x2 - x1, y2 - y1
                if bw < 5 or bh < 5:
                    continue

                cls_name = self.model.names.get(cls_id, f"cls_{cls_id}").lower()

                # IMPORTANT: Prioritize name-based matching over cls_id-based COCO matching
                # to avoid conflicts (e.g. custom model's 'face' at cls_id=1 being matched as COCO 'bicycle')

                # --- 1. PERSON / FACE ---
                if cls_name in PERSON_NAMES:
                    persons_found.append({
                        "cls_name": cls_name, "bbox": (x1, y1, x2, y2), "conf": det_conf
                    })
                
                # --- 2. LICENSE PLATE (Direct Detection) ---
                elif cls_name in PLATE_NAMES:
                    direct_plates.append({
                        "bbox": (x1, y1, x2, y2), "conf": det_conf
                    })

                # --- 3. ROLLING DOORS ---
                elif cls_name in DOOR_NAMES:
                    doors_found.append({
                        "cls_name": cls_name, "bbox": (x1, y1, x2, y2), "conf": det_conf
                    })

                # --- 4. VEHICLES (name match first, then COCO cls_id fallback) ---
                elif cls_name in VEHICLE_NAMES:
                    vehicles_found.append({
                        "cls_id": cls_id,
                        "cls_name": cls_name,
                        "bbox": (x1, y1, x2, y2),
                        "conf": det_conf
                    })
                
                # --- 5. COCO cls_id fallback (only when NOT a custom model) ---
                elif not is_custom and cls_id in PERSON_CLASS:
                    persons_found.append({
                        "cls_name": "person", "bbox": (x1, y1, x2, y2), "conf": det_conf
                    })
                elif not is_custom and cls_id in VEHICLE_CLASSES:
                    vehicles_found.append({
                        "cls_id": cls_id,
                        "cls_name": VEHICLE_CLASSES.get(cls_id, cls_name),
                        "bbox": (x1, y1, x2, y2),
                        "conf": det_conf
                    })
                
                # --- 6. FALLBACK (MISC) ---
                else:
                    box_area = bw * bh
                    if box_area > img_area * 0.05: # Large unknown object could be a vehicle
                        vehicles_found.append({
                            "cls_id": cls_id,
                            "cls_name": cls_name,
                            "bbox": (x1, y1, x2, y2),
                            "conf": det_conf
                        })

        # --- PROCESS DIRECT PLATES ---
        for dp in direct_plates:
            px1, py1, px2, py2 = dp["bbox"]
            p_crop = img[py1:py2, px1:px2]
            if p_crop.size == 0: continue
            
            p_text, p_ocr_conf = self._run_optimized_ocr(p_crop)
            if p_ocr_conf > 0.2:
                detections.append({
                    "type": "vehicle", "cls_name": "license_plate",
                    "bbox": dp["bbox"], "det_conf": dp["conf"],
                    "plate_text": p_text, "ocr_conf": p_ocr_conf,
                    "final_conf": dp["conf"] * p_ocr_conf,
                    "crop": p_crop, "enhanced": enhance_plate(p_crop),
                    "whitelisted": _is_plate_whitelisted(p_text),
                })
                # Draw direct plate — green if whitelisted, yellow if unknown
                if draw:
                    is_wl = _is_plate_whitelisted(p_text)
                    color = (0, 255, 0) if is_wl else (0, 255, 255)
                    draw_label(annotated, px1, py1, px2, py2, p_text, color)
                # Log unknown plate + OCR sample
                _handle_unknown_plate(p_text, p_ocr_conf, p_crop)

        # --- PROCESS VEHICLES (2-STAGE) ---
        for veh in vehicles_found:
            x1, y1, x2, y2 = veh["bbox"]
            crop = img[y1:y2, x1:x2]
            if crop.size == 0: continue

            plate_text, ocr_conf, plate_crop, enhanced = self._ocr_vehicle_crop(crop)
            final_conf = veh["conf"] * ocr_conf if ocr_conf > 0 else veh["conf"]

            is_wl = _is_plate_whitelisted(plate_text) if plate_text else False
            color = (0, 255, 0) if (plate_text and is_wl) else (0, 255, 255) if plate_text else (0, 165, 255)
            lbl = f"{veh['cls_name'].upper()} {plate_text}" if plate_text else f"{veh['cls_name'].upper()} ({veh['conf']:.0%})"
            if draw:
                draw_label(annotated, x1, y1, x2, y2, lbl, color)

            detections.append({
                "type": "vehicle", "cls_name": veh["cls_name"],
                "bbox": (x1, y1, x2, y2), "det_conf": veh["conf"],
                "plate_text": plate_text, "ocr_conf": ocr_conf,
                "final_conf": final_conf, "crop": plate_crop, "enhanced": enhanced,
                "whitelisted": is_wl,
            })
            # Log unknown plate + OCR sample
            if plate_text:
                _handle_unknown_plate(plate_text, ocr_conf, plate_crop)

        # --- PROCESS DOORS ---
        for door in doors_found:
            x1, y1, x2, y2 = door["bbox"]
            color = (255, 0, 255) # Magenta for doors
            if draw:
                draw_label(annotated, x1, y1, x2, y2, f"{door['cls_name'].upper()} ({door['conf']:.0%})", color)
            detections.append({
                "type": "door", "cls_name": door["cls_name"],
                "bbox": (x1, y1, x2, y2), "det_conf": door["conf"]
            })

        # --- FALLBACK: SCAN FULL IMAGE ---
        aggressive = config.get('aggressive_ocr', False)
        if not [d for d in detections if d.get("plate_text")]:
            if vehicles_found or aggressive:
                plate_text, ocr_conf, plate_crop, enhanced = self._ocr_full_image(img)
                if ocr_conf > 0.3:
                    h_a, w_a = annotated.shape[:2]
                    is_wl_fs = _is_plate_whitelisted(plate_text)
                    # Draw a notification bar at the bottom for full scan
                    if draw:
                        fs_color = (0, 255, 0) if is_wl_fs else (0, 255, 255)
                        draw_label(annotated, 10, h_a - 50, 400, h_a - 10, f"SCAN: {plate_text}", fs_color)
                    detections.append({
                        "type": "vehicle", "cls_name": "direct_scan",
                        "bbox": (0, 0, w_a, h_a), "det_conf": 1.0,
                        "plate_text": plate_text, "ocr_conf": ocr_conf,
                        "final_conf": ocr_conf, "crop": plate_crop, "enhanced": enhanced,
                        "whitelisted": is_wl_fs,
                    })

        # --- PROCESS PERSONS ---
        for p in persons_found:
            x1, y1, x2, y2 = p["bbox"]
            color = (255, 180, 0)
            if draw:
                draw_label(annotated, x1, y1, x2, y2, f"PERSON ({p['conf']:.0%})", color)
            detections.append({
                "type": "person", "cls_name": "person",
                "bbox": (x1, y1, x2, y2), "det_conf": p["conf"]
            })

        det_ms = (time.perf_counter() - t0) * 1000
        return annotated, detections, det_ms

    def _ocr_vehicle_crop(self, crop: np.ndarray) -> tuple:
        """Run OCR on a vehicle crop using efficient ROI strategies."""
        best_text, best_conf = "", 0.0
        h, w = crop.shape[:2]
        
        # Strategy 1: Bottom 40% of vehicle (where plates usually are)
        # Much more efficient than contour search on full crop
        bottom_roi = crop[int(h * 0.6):, :]
        if bottom_roi.size > 0:
            text, conf = self._run_optimized_ocr(bottom_roi)
            if conf > 0.4:
                return text, conf, bottom_roi, enhance_plate(bottom_roi)
        
        # Strategy 2: Full crop fallback
        text, conf = self._run_optimized_ocr(crop)
        return text, conf, crop, enhance_plate(crop)

    def _run_optimized_ocr(self, img: np.ndarray) -> tuple[str, float]:
        """Run OCR once on a pre-processed image for maximum efficiency."""
        if self.ocr is None: return "", 0.0
        h, w = img.shape[:2]
        # Always upscale small crops — PaddleOCR needs ~150px height for accuracy
        if h < 150:
            scale = max(2.0, 150.0 / h)
            proc = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        else:
            proc = img
        return read_plate_ocr(self.ocr, proc)

    def _ocr_full_image(self, img: np.ndarray) -> tuple:
        """Efficient full frame scan as a last resort."""
        # We skip deep ROI search here for performance on high-res frames
        # Just run OCR once on the full frame
        return self._run_optimized_ocr(img) + (img, enhance_plate(img))

    def detect_faces(self, img: np.ndarray, known_dir: str, draw: bool = True):
        if self.face_app is None:
            return img.copy() if draw else img, [], 0

        # Convert to absolute path to avoid WD issues in threads
        known_dir = os.path.abspath(known_dir)
        
        mtime = 0
        if os.path.exists(known_dir):
            mtime = os.path.getmtime(known_dir)
        else:
            self._last_log = f"ERROR: Thư mục không tồn tại: {known_dir}"
            
        now = time.time()
        # Use a load lock to prevent thundering herd (multiple threads loading same dir)
        with self._load_lock:
            info = self._cache_info.get(known_dir, {})
            last_ts = info.get("ts", 0)
            last_mtime = info.get("mtime", 0)
            now = time.time()
            if (known_dir not in self._face_cache or 
                now - last_ts > 120 or # Reload every 2 mins max
                mtime > last_mtime + 0.5):
                
                msg = f"Đang nạp dữ liệu từ: {os.path.basename(known_dir)}..."
                self._last_log = msg
                print(f"[INFO] {msg}")
                new_known: dict[str, np.ndarray] = {}
                if os.path.isdir(known_dir):
                    people = os.listdir(known_dir)
                    for person in people:
                        pdir = os.path.join(known_dir, person)
                        if not os.path.isdir(pdir): continue
                        
                        embs = []
                        images = [f for f in os.listdir(pdir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
                        for f in images:
                            fpath = os.path.join(pdir, f)
                            pimg = cv2.imread(fpath)
                            if pimg is None: continue
                            with self._lock:
                                faces = self.face_app.get(pimg)
                            if faces: 
                                embs.append(faces[0].normed_embedding)
                        
                        if embs:
                            new_known[person] = np.mean(embs, axis=0)
                            print(f"[SUCCESS] Loaded {person} ({len(embs)} views)")
                
                self._face_cache[known_dir] = new_known
                self._cache_info[known_dir + "_ts"] = now
                self._cache_info[known_dir + "_mtime"] = mtime
                self._last_log = f"Đã tải {len(new_known)} khuôn mặt lúc {time.strftime('%H:%M:%S')}"
        
        known = self._face_cache.get(known_dir, {})

        t0 = time.perf_counter()
        with self._lock:
            faces = self.face_app.get(img)
        det_ms = (time.perf_counter() - t0) * 1000

        annotated = img.copy() if draw else img
        detections = []

        for face in faces:
            bbox = face.bbox.astype(int)
            x1, y1, x2, y2 = bbox[0], bbox[1], bbox[2], bbox[3]
            emb = face.normed_embedding

            name, sim = "STRANGER", 0.0
            top_sims = []
            for pname, ref in known.items():
                s = float(np.dot(emb, ref))
                top_sims.append((pname, s))
                if s > sim:
                    sim = s
                    # Buffalo_l is more precise, using 0.50 as reliable threshold per user request
                    name = pname if s > 0.50 else "STRANGER"
            
            # Diagnostic: Update log with top match for visibility
            if top_sims:
                top_sims.sort(key=lambda x: x[1], reverse=True)
                top_p, top_s = top_sims[0]
                self._last_log = f"Match gần nhất: {top_p} ({top_s:.2f})"

            # Fetch metadata from DB if it's not a stranger
            full_name = "???"
            username = "???"
            if name != "STRANGER":
                if name not in self._db_identity_cache:
                    try:
                        p_info = self.db.get_person_by_identity(name)
                        if p_info:
                            # Ensure we get actual values and not None
                            f_n = str(p_info.get('full_name') or "???")
                            u_n = str(p_info.get('username') or "???")
                            self._db_identity_cache[name] = {"full_name": f_n, "username": u_n}
                            # Log finding for terminal debugging
                            print(f"[DEBUG] Face match: ID={name} -> DB_Name={f_n}, DB_User={u_n}")
                        else:
                            self._db_identity_cache[name] = {"full_name": "???", "username": "???"}
                    except Exception as e:
                        print(f"[ERROR] DB lookup failed for {name}: {e}")
                        self._db_identity_cache[name] = {"full_name": "???", "username": "???"}
                
                cached_info = self._db_identity_cache.get(name, {"full_name": "???", "username": "???"})
                full_name = cached_info["full_name"]
                username = cached_info["username"]
            
            # --- STRANGER: crop, save, log DB ---
            # Only log truly unknown faces (sim < 0.25)
            # Faces with 0.25-0.40 are likely known people with partial occlusion
            face_crop = None
            image_path = None
            if name == "STRANGER" and sim < 0.25:
                img_h, img_w = img.shape[:2]
                face_crop = img[max(0, y1):min(img_h, y2), max(0, x1):min(img_w, x2)]
                cam_key = f"stranger_{getattr(self, '_current_camera_id', 'default')}"
                now_t = time.time()
                if now_t - self._seen_strangers.get(cam_key, 0) > self._stranger_cooldown:
                    self._seen_strangers[cam_key] = now_t
                    try:
                        os.makedirs("data/snapshots/strangers", exist_ok=True)
                        fname = f"stranger_{int(now_t)}_{x1}_{y1}.jpg"
                        image_path = os.path.join("data/snapshots/strangers", fname)
                        cv2.imwrite(image_path, face_crop)
                    except Exception as e:
                        image_path = None
                        logger.warning("Failed to save stranger snapshot: %s", e)
                    try:
                        self.db.log_stranger(None, sim, image_path)
                    except Exception as e:
                        logger.warning("Failed to log stranger: %s", e)

            detections.append({
                "name": str(name),
                "full_name": full_name,
                "username": username,
                "similarity": float(sim),
                "bbox": (x1, y1, x2, y2),
                "face_crop": face_crop if name == "STRANGER" else None,
                "image_path": image_path,
            })

            color = (0, 200, 0) if name != "STRANGER" else (0, 0, 220)
            
            # Display format: [ID] Full Name
            display_name = f"[{name}] {full_name}" if full_name != "???" else f"ID:{name}"
            display_label = f"{display_name} ({sim:.2f})" if name != "STRANGER" else f"STRANGER ({sim:.2f})"
            if draw:
                draw_label(annotated, x1, y1, x2, y2, display_label, color)

        return annotated, detections, det_ms
