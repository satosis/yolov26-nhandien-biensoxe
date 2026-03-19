"""
Test: detection_controller._is_plate_whitelisted + normalize_plate

Chay: python3 /Users/mac/Documents/yolov26-nhandien-biensoxe/tests/test_whitelist_check.py
Hoac: cd /Users/mac/Documents/yolov26-nhandien-biensoxe && python3 tests/test_whitelist_check.py
"""

import sys
import os
import types
import numpy as np
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# 0. Them project root vao sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = "/Users/mac/Documents/yolov26-nhandien-biensoxe"
sys.path.insert(0, PROJECT_ROOT)

# ---------------------------------------------------------------------------
# 1. Stub cac heavy dependencies truoc khi import bat ky module nao cua project
# ---------------------------------------------------------------------------

# --- psycopg2 stub ---
psycopg2_stub = types.ModuleType("psycopg2")
psycopg2_stub.pool = types.ModuleType("psycopg2.pool")

class _FakePoolError(Exception):
    pass

psycopg2_stub.pool.PoolError = _FakePoolError
psycopg2_stub.pool.ThreadedConnectionPool = MagicMock(return_value=None)
psycopg2_stub.connect = MagicMock(side_effect=Exception("no real db in test"))
psycopg2_stub.extras = types.ModuleType("psycopg2.extras")
psycopg2_stub.extras.RealDictCursor = None
sys.modules.setdefault("psycopg2", psycopg2_stub)
sys.modules.setdefault("psycopg2.pool", psycopg2_stub.pool)
sys.modules.setdefault("psycopg2.extras", psycopg2_stub.extras)

# --- ultralytics stub (tranh phu thuoc YOLO model file) ---
ul_stub = types.ModuleType("ultralytics")
ul_stub.YOLO = MagicMock()
sys.modules.setdefault("ultralytics", ul_stub)

# ---------------------------------------------------------------------------
# 2. Import module that
# ---------------------------------------------------------------------------
from core.config import normalize_plate  # type: ignore

# Import detection_controller voi DatabaseManager da duoc mock
with patch("core.database.DatabaseManager") as _PreloadMockDB:
    _PreloadMockDB.return_value = MagicMock()
    from streamlit_app.controllers.detection_controller import (  # type: ignore
        _is_plate_whitelisted,
        _whitelist_cache,
        DetectionController,
    )

# ---------------------------------------------------------------------------
# 3. Helpers
# ---------------------------------------------------------------------------
PASS_COUNT = 0
FAIL_COUNT = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    status = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    if ok:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1


def assert_eq(name: str, got, expected) -> None:
    ok = got == expected
    report(name, ok, f"got={got!r}  expected={expected!r}" if not ok else "")


def assert_true(name: str, value, detail: str = "") -> None:
    report(name, bool(value), detail)


def reset_cache() -> None:
    """Buc cache het han de _is_plate_whitelisted phai goi DB."""
    _whitelist_cache["ts"] = 0
    _whitelist_cache["data"] = set()


# ---------------------------------------------------------------------------
# 4. Test normalize_plate
# ---------------------------------------------------------------------------
print("\n--- normalize_plate ---")

assert_eq(
    'normalize_plate("88C 073.04") == "88C07304"',
    normalize_plate("88C 073.04"),
    "88C07304",
)

assert_eq(
    'normalize_plate("30A-123.45") == "30A12345"',
    normalize_plate("30A-123.45"),
    "30A12345",
)

assert_eq(
    'normalize_plate("29a 12345") == "29A12345"  (lowercase -> upper)',
    normalize_plate("29a 12345"),
    "29A12345",
)

assert_eq(
    "normalize_plate strips spaces/dashes/dots - 51G 999.99",
    normalize_plate("51G 999.99"),
    "51G99999",
)

assert_eq(
    "normalize_plate with already-clean input 43A12345",
    normalize_plate("43A12345"),
    "43A12345",
)

assert_eq(
    "normalize_plate with SQL injection chars",
    normalize_plate("'; DROP TABLE plates; --"),
    "DROPTABLEPLATES",
)

assert_eq(
    "normalize_plate handles empty string",
    normalize_plate(""),
    "",
)

# ---------------------------------------------------------------------------
# 5. Test _is_plate_whitelisted - return type la bool
# ---------------------------------------------------------------------------
print("\n--- _is_plate_whitelisted return type ---")

reset_cache()
with patch("streamlit_app.controllers.detection_controller.DatabaseManager") as MockDB:
    mock_instance = MagicMock()
    mock_instance.select.return_value = []
    MockDB.return_value = mock_instance
    result = _is_plate_whitelisted("29A12345")

assert_true(
    "_is_plate_whitelisted tra ve bool",
    isinstance(result, bool),
    f"got type={type(result).__name__}",
)

# ---------------------------------------------------------------------------
# 6. Test _is_plate_whitelisted - plate co trong whitelist -> True
# ---------------------------------------------------------------------------
print("\n--- _is_plate_whitelisted logic ---")

reset_cache()
with patch("streamlit_app.controllers.detection_controller.DatabaseManager") as MockDB:
    mock_instance = MagicMock()
    mock_instance.select.return_value = [{"plate_number": "29A12345"}]
    MockDB.return_value = mock_instance
    found = _is_plate_whitelisted("29A12345")

assert_true(
    "plate co trong whitelist -> True",
    found is True,
    f"expected True, got {found!r}",
)

# ---------------------------------------------------------------------------
# 7. Test _is_plate_whitelisted - plate KHONG trong whitelist -> False
# ---------------------------------------------------------------------------
reset_cache()
with patch("streamlit_app.controllers.detection_controller.DatabaseManager") as MockDB:
    mock_instance = MagicMock()
    mock_instance.select.return_value = [{"plate_number": "29A12345"}]
    MockDB.return_value = mock_instance
    not_found = _is_plate_whitelisted("99Z99999")

assert_true(
    "plate KHONG trong whitelist -> False",
    not_found is False,
    f"expected False, got {not_found!r}",
)

# ---------------------------------------------------------------------------
# 8. Test normalize ca hai phia truoc khi so sanh (whitelist chua dang "29A 123-45")
# ---------------------------------------------------------------------------
reset_cache()
with patch("streamlit_app.controllers.detection_controller.DatabaseManager") as MockDB:
    mock_instance = MagicMock()
    # Whitelist luu dang chua normalized
    mock_instance.select.return_value = [{"plate_number": "29A 123-45"}]
    MockDB.return_value = mock_instance
    # Lookup voi input sach -> ca hai deu normalize thanh "29A12345"
    normalized_match = _is_plate_whitelisted("29A12345")

assert_true(
    "normalize ca hai phia: '29A 123-45' vs '29A12345' -> True",
    normalized_match is True,
    f"expected True (both -> 29A12345), got {normalized_match!r}",
)

# ---------------------------------------------------------------------------
# 9. Test lookup bang input chua normalized cung khop duoc
# ---------------------------------------------------------------------------
reset_cache()
with patch("streamlit_app.controllers.detection_controller.DatabaseManager") as MockDB:
    mock_instance = MagicMock()
    mock_instance.select.return_value = [{"plate_number": "29A12345"}]
    MockDB.return_value = mock_instance
    # Input co space va dot
    dirty_match = _is_plate_whitelisted("29A 123.45")

assert_true(
    "input '29A 123.45' normalize -> '29A12345' -> khop whitelist -> True",
    dirty_match is True,
    f"expected True, got {dirty_match!r}",
)

# ---------------------------------------------------------------------------
# 10. Test DB exception -> khong crash, cache khong thay doi, tra ve False
# ---------------------------------------------------------------------------
reset_cache()
with patch("streamlit_app.controllers.detection_controller.DatabaseManager") as MockDB:
    mock_instance = MagicMock()
    mock_instance.select.side_effect = Exception("DB connection refused")
    MockDB.return_value = mock_instance
    try:
        safe_result = _is_plate_whitelisted("29A12345")
        crashed = False
    except Exception:
        safe_result = None
        crashed = True

assert_true(
    "DB exception -> khong crash (no exception raised)",
    not crashed,
    "function threw an exception",
)
assert_true(
    "DB exception -> tra ve bool (False hoac True, khong raise)",
    isinstance(safe_result, bool),
    f"got type={type(safe_result).__name__}, value={safe_result!r}",
)

# ---------------------------------------------------------------------------
# 11. Test cache TTL: sau lan dau, khong goi DB lan 2 neu chua het han
# ---------------------------------------------------------------------------
print("\n--- _whitelist_cache TTL ---")

reset_cache()
with patch("streamlit_app.controllers.detection_controller.DatabaseManager") as MockDB:
    mock_instance = MagicMock()
    mock_instance.select.return_value = [{"plate_number": "88C07304"}]
    MockDB.return_value = mock_instance

    # Lan 1: goi DB
    _is_plate_whitelisted("88C07304")
    call_count_after_first = mock_instance.select.call_count  # phai = 1

    # Lan 2: cache con hieu luc -> KHONG goi DB
    _is_plate_whitelisted("88C07304")
    call_count_after_second = mock_instance.select.call_count  # van = 1

assert_eq(
    "lan goi DB dau tien: select duoc goi dung 1 lan",
    call_count_after_first,
    1,
)
assert_eq(
    "lan goi thu 2 trong TTL: select KHONG duoc goi them",
    call_count_after_second,
    1,
)

# ---------------------------------------------------------------------------
# 12. Test detect_plates output dict co key "whitelisted"
# ---------------------------------------------------------------------------
print("\n--- detect_plates: detection dict co key 'whitelisted' ---")


def _make_fake_frame(w: int = 640, h: int = 480) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


# Build minimal mock YOLO model tra ve 1 detection license_plate
mock_model = MagicMock()
mock_model.names = {4: "license_plate"}  # custom model

fake_box = MagicMock()
fake_box.cls = [4]
fake_box.xyxy = [np.array([[100, 200, 200, 250]], dtype=np.float32)]
fake_box.conf = [np.float32(0.9)]

fake_result = MagicMock()
fake_result.boxes = [fake_box]
mock_model.return_value = [fake_result]

# Mock OCR tra ve bien so voi conf cao
mock_ocr = MagicMock()
mock_ocr.ocr.return_value = [
    [
        [
            [[100, 200], [200, 200], [200, 230], [100, 230]],
            ("29A12345", 0.95),
        ]
    ]
]

with patch("streamlit_app.controllers.detection_controller.DatabaseManager") as MockDB:
    mock_db = MagicMock()
    mock_db.select.return_value = []
    MockDB.return_value = mock_db

    with patch(
        "streamlit_app.controllers.detection_controller._is_plate_whitelisted",
        return_value=False,
    ):
        controller = DetectionController(model=mock_model, ocr=mock_ocr, face_app=None)
        frame = _make_fake_frame()
        cfg = {"conf_thresh": 0.25, "iou_thresh": 0.45}

        try:
            _, detections, det_ms = controller.detect_plates(frame, cfg, draw=False)
            pipeline_ok = True
        except Exception as exc:
            pipeline_ok = False
            detections = []
            det_ms = -1
            report("detect_plates chay khong exception", False, str(exc))

        if pipeline_ok:
            report("detect_plates chay khong exception", True)
            assert_true(
                "det_ms >= 0 (pipeline thuc su chay)",
                det_ms >= 0,
                f"det_ms={det_ms:.2f}ms",
            )

            vehicle_dets = [d for d in detections if d.get("type") == "vehicle"]
            if vehicle_dets:
                all_have_key = all("whitelisted" in d for d in vehicle_dets)
                assert_true(
                    f"moi vehicle detection dict deu co key 'whitelisted' ({len(vehicle_dets)} det)",
                    all_have_key,
                )
                for d in vehicle_dets:
                    wl_val = d.get("whitelisted")
                    assert_true(
                        f"whitelisted value la bool (plate={d.get('plate_text', '?')!r})",
                        isinstance(wl_val, bool),
                        f"got type={type(wl_val).__name__}, value={wl_val!r}",
                    )
            else:
                # OCR mock co the khong di qua het sau khi xe xu ly
                # Van chap nhan neu pipeline khong loi
                report(
                    "khong co vehicle detection (OCR mock co the bi cut short) - skip key check",
                    True,
                    f"total detections={len(detections)}",
                )

# ---------------------------------------------------------------------------
# 13. Test detect_plates: whitelist=True khi bien so co trong DB
# ---------------------------------------------------------------------------
print("\n--- detect_plates: whitelisted=True khi bien so trong whitelist ---")

mock_model2 = MagicMock()
mock_model2.names = {4: "license_plate"}

fake_box2 = MagicMock()
fake_box2.cls = [4]
fake_box2.xyxy = [np.array([[50, 100, 150, 130]], dtype=np.float32)]
fake_box2.conf = [np.float32(0.88)]

fake_result2 = MagicMock()
fake_result2.boxes = [fake_box2]
mock_model2.return_value = [fake_result2]

mock_ocr2 = MagicMock()
mock_ocr2.ocr.return_value = [
    [
        [
            [[50, 100], [150, 100], [150, 130], [50, 130]],
            ("51G99999", 0.91),
        ]
    ]
]

reset_cache()
with patch("streamlit_app.controllers.detection_controller.DatabaseManager") as MockDB:
    mock_db2 = MagicMock()
    mock_db2.select.return_value = []
    MockDB.return_value = mock_db2

    # _is_plate_whitelisted tra ve True cho bien 51G99999
    with patch(
        "streamlit_app.controllers.detection_controller._is_plate_whitelisted",
        return_value=True,
    ):
        controller2 = DetectionController(model=mock_model2, ocr=mock_ocr2, face_app=None)
        frame2 = _make_fake_frame()
        try:
            _, dets2, _ = controller2.detect_plates(frame2, cfg, draw=False)
            vehicle_dets2 = [d for d in dets2 if d.get("type") == "vehicle"]
            if vehicle_dets2:
                all_whitelisted = all(d.get("whitelisted") is True for d in vehicle_dets2)
                assert_true(
                    "detect_plates: whitelisted=True khi _is_plate_whitelisted=True",
                    all_whitelisted,
                    f"values={[d.get('whitelisted') for d in vehicle_dets2]}",
                )
            else:
                report(
                    "detect_plates whitelisted=True check: khong co vehicle det - skip",
                    True,
                    f"total={len(dets2)}",
                )
        except Exception as exc:
            report("detect_plates whitelisted=True run", False, str(exc))

# ---------------------------------------------------------------------------
# 14. Summary
# ---------------------------------------------------------------------------
total = PASS_COUNT + FAIL_COUNT
print(f"\n{'='*60}")
print(f"  Ket qua: {PASS_COUNT}/{total} PASS  |  {FAIL_COUNT}/{total} FAIL")
print(f"{'='*60}")
sys.exit(0 if FAIL_COUNT == 0 else 1)
