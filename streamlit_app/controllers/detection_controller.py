import time
import re
import cv2
import numpy as np
import os
import threading
from core.database import DatabaseManager

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
    """Run PaddleOCR on a crop and extract plate text."""
    if ocr is None:
        return "", 0.0
    try:
        result = ocr.ocr(crop, cls=True)
    except Exception:
        return "", 0.0
    if not result or not result[0]:
        return "", 0.0
    texts, confs = [], []
    for line in result[0]:
        if line and len(line) >= 2:
            texts.append(line[1][0])
            confs.append(float(line[1][1]))
    if not texts:
        return "", 0.0
    raw_text = " ".join(texts).upper()
    # Clean up: keep only alphanumerics, dots, dashes
    cleaned = re.sub(r'[^A-Z0-9.\-]', '', raw_text)
    avg_conf = sum(confs) / len(confs)
    return cleaned, avg_conf


# Removed find_plate_region: Redundant and slow on ARM when using YOLO plate detection.


def draw_label(img, x1, y1, label, color):
    """Draw label with background on image."""
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.rectangle(img, (x1, max(y1 - th - 8, 0)), (x1 + tw + 6, y1), color, -1)
    cv2.putText(img, label, (x1 + 3, max(y1 - 4, th)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)


class DetectionController:
    def __init__(self, model, ocr, face_app):
        self.model = model
        self.ocr = ocr
        self.face_app = face_app
        self.db = DatabaseManager()
        self._face_cache = {} # {abs_path: {person_name: embedding}}
        self._cache_info = {} # {abs_path: {"ts": time, "mtime": mtime}}
        # Use shared lock from face_app if available
        self._lock = getattr(face_app, 'lock', threading.Lock())
        self._load_lock = threading.Lock() # Lock purely for cache loading
        self._last_log = "Chưa khởi tạo cache"

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
            self._last_log = "Đã xóa bộ nhớ (Cache cleared)."

    def detect_plates(self, img: np.ndarray, config: dict):
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
        annotated = img.copy()
        
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
            
            p_text, p_ocr_conf = self._best_ocr(p_crop)
            if p_ocr_conf > 0.2:
                detections.append({
                    "type": "vehicle", "cls_name": "license_plate",
                    "bbox": dp["bbox"], "det_conf": dp["conf"],
                    "plate_text": p_text, "ocr_conf": p_ocr_conf,
                    "final_conf": dp["conf"] * p_ocr_conf,
                    "crop": p_crop, "enhanced": enhance_plate(p_crop)
                })
                # Draw direct plate
                cv2.rectangle(annotated, (px1, py1), (px2, py2), (0, 255, 0), 2)
                draw_label(annotated, px1, py1, f"PLATE: {p_text}", (0, 255, 0))

        # --- PROCESS VEHICLES (2-STAGE) ---
        for veh in vehicles_found:
            x1, y1, x2, y2 = veh["bbox"]
            # Skip if this vehicle already contains a direct_plate (optional optimization)
            
            crop = img[y1:y2, x1:x2]
            if crop.size == 0: continue

            plate_text, ocr_conf, plate_crop, enhanced = self._ocr_vehicle_crop(crop)
            final_conf = veh["conf"] * ocr_conf if ocr_conf > 0 else veh["conf"]

            # Draw vehicle
            color = (0, 200, 0) if plate_text else (0, 165, 255)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            lbl = f"{veh['cls_name'].upper()} {plate_text} ({ocr_conf:.0%})" if plate_text else f"{veh['cls_name'].upper()} ({veh['conf']:.0%})"
            draw_label(annotated, x1, y1, lbl, color)

            detections.append({
                "type": "vehicle", "cls_name": veh["cls_name"],
                "bbox": (x1, y1, x2, y2), "det_conf": veh["conf"],
                "plate_text": plate_text, "ocr_conf": ocr_conf,
                "final_conf": final_conf, "crop": plate_crop, "enhanced": enhanced,
            })

        # --- PROCESS DOORS ---
        for door in doors_found:
            x1, y1, x2, y2 = door["bbox"]
            color = (255, 0, 255) # Magenta for doors
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            draw_label(annotated, x1, y1, f"{door['cls_name'].upper()} ({door['conf']:.0%})", color)
            detections.append({
                "type": "door", "cls_name": door["cls_name"],
                "bbox": (x1, y1, x2, y2), "det_conf": door["conf"]
            })

        # --- FALLBACK: SCAN FULL IMAGE ---
        # Optimization: Only scan full image if YOLO found vehicles but no plates, 
        # or if forced by config. This prevents hangs on high-res selfies.
        aggressive = config.get('aggressive_ocr', False)
        if not [d for d in detections if d.get("plate_text")]:
            if vehicles_found or aggressive:
                plate_text, ocr_conf, plate_crop, enhanced = self._ocr_full_image(img)
                if ocr_conf > 0.3:
                    h_a, w_a = annotated.shape[:2]
                    draw_label(annotated, 10, h_a - 40, f"FULL-SCAN: {plate_text}", (0, 255, 255))
                    detections.append({
                        "type": "vehicle", "cls_name": "direct_scan",
                        "bbox": (0, 0, w_a, h_a), "det_conf": 1.0,
                        "plate_text": plate_text, "ocr_conf": ocr_conf,
                        "final_conf": ocr_conf, "crop": plate_crop, "enhanced": enhanced,
                    })

        # --- PROCESS PERSONS ---
        for p in persons_found:
            x1, y1, x2, y2 = p["bbox"]
            color = (255, 180, 0)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            draw_label(annotated, x1, y1, f"PERSON ({p['conf']:.0%})", color)
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
        # Pre-enhance only if image is small
        h, w = img.shape[:2]
        if h < 50 or w < 120:
            proc = enhance_plate(img)
        else:
            proc = img
        return read_plate_ocr(self.ocr, proc)

    def _ocr_full_image(self, img: np.ndarray) -> tuple:
        """Efficient full frame scan as a last resort."""
        # We skip deep ROI search here for performance on high-res frames
        # Just run OCR once on the full frame
        return self._run_optimized_ocr(img) + (img, enhance_plate(img))

    def detect_faces(self, img: np.ndarray, known_dir: str):
        if self.face_app is None:
            return img.copy(), [], 0

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

        annotated = img.copy()
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
                    # Buffalo_l is more precise, using 0.40 as reliable threshold
                    name = pname if s > 0.40 else "STRANGER"
            
            # Diagnostic: Update log with top match for visibility
            if top_sims:
                top_sims.sort(key=lambda x: x[1], reverse=True)
                top_p, top_s = top_sims[0]
                self._last_log = f"Match gần nhất: {top_p} ({top_s:.2f})"

            # Fetch metadata from DB if it's not a stranger
            full_name = "???"
            username = "???"
            if name != "STRANGER":
                try:
                    p_info = self.db.get_person_by_identity(name)
                    if p_info:
                        # Ensure we get actual values and not None
                        full_name = str(p_info.get('full_name') or "???")
                        username = str(p_info.get('username') or "???")
                        # Log finding for terminal debugging
                        print(f"[DEBUG] Face match: ID={name} -> DB_Name={full_name}, DB_User={username}")
                except Exception as e:
                    print(f"[ERROR] DB lookup failed for {name}: {e}")
            
            detections.append({
                "name": str(name), 
                "full_name": full_name,
                "username": username,
                "similarity": float(sim), 
                "bbox": (x1, y1, x2, y2)
            })

            color = (0, 200, 0) if name != "STRANGER" else (0, 0, 220)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            
            # Display format: [ID] Full Name
            display_name = f"[{name}] {full_name}" if full_name != "???" else f"ID:{name}"
            display_label = f"{display_name} ({sim:.2f})" if name != "STRANGER" else f"STRANGER ({sim:.2f})"
            draw_label(annotated, x1, y1, display_label, color)

        return annotated, detections, det_ms
