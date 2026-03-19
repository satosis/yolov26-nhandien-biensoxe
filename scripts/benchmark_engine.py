import cv2
import numpy as np
import time
import os
import sys

# Add current dir to path to import config
sys.path.append(os.getcwd())

# Fix for E2ELoss error when loading YOLOv10 models
import ultralytics.utils.loss
if not hasattr(ultralytics.utils.loss, "E2ELoss"):
    class E2ELoss:
        pass
    ultralytics.utils.loss.E2ELoss = E2ELoss

def benchmark():
    from ultralytics import YOLO
    from paddleocr import PaddleOCR
    import core.config as cfg

    print("=== System Benchmark & Optimization Test ===")
    
    # 1. Test Model Loading
    print("\n[1/4] Loading models...")
    pt_path = "models/yolo26_plate_people_door.pt"
    onnx_path = "models/yolo26_plate_people_door.onnx"
    int8_path = "models/yolo26_plate_people_door_int8.onnx"
    
    models = {}
    if os.path.exists(pt_path):
        models['PT'] = YOLO(pt_path)
    if os.path.exists(onnx_path):
        models['ONNX'] = YOLO(onnx_path, task='detect')
    if os.path.exists(int8_path):
        models['INT8'] = YOLO(int8_path, task='detect')
    
    ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False, use_gpu=False)

    # 2. Create synthetic test frame
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    # Simulate a car with a plate
    cv2.rectangle(frame, (800, 600), (1100, 750), (200, 200, 200), -1)
    cv2.putText(frame, "30A-123.45", (820, 700), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    
    # 3. Benchmark YOLO at different resolutions
    print("\n[2/4] Benchmarking YOLO Detection...")
    results = []
    for name, model in models.items():
        resolutions = [640] if name == 'ONNX' else [320, 640]
        for res in resolutions:
            try:
                # Warmup
                model(frame, imgsz=res, verbose=False)
                
                t0 = time.time()
                iters = 10
                for _ in range(iters):
                    model(frame, imgsz=res, verbose=False)
                t1 = time.time()
                avg = (t1 - t0) / iters
                fps = 1.0 / avg
                print(f"  - {name} @ {res}px: {avg:.4f}s ({fps:.2f} FPS)")
                results.append({'name': name, 'res': res, 'time': avg})
            except Exception as e:
                print(f"  - {name} @ {res}px: FAILED ({e})")

    # 4. Benchmark OCR
    print("\n[3/4] Benchmarking OCR...")
    plate_crop = frame[600:750, 800:1100]
    
    # Raw OCR
    t0 = time.time()
    for _ in range(5):
        ocr.ocr(plate_crop, cls=False)
    avg_raw = (time.time() - t0) / 5
    print(f"  - OCR Raw: {avg_raw:.4f}s")
    
    # Enhanced OCR (2x)
    enhanced = cv2.resize(plate_crop, (0,0), fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
    t0 = time.time()
    for _ in range(5):
        ocr.ocr(enhanced, cls=False)
    avg_enh = (time.time() - t0) / 5
    print(f"  - OCR Enhanced (2x): {avg_enh:.4f}s")

    # 5. Conclusion & Recommendations
    print("\n[4/4] Conclusion & Recommendations:")
    # Find best YOLO
    best_yolo = min(results, key=lambda x: x['time'])
    print(f"  => Best Detection: {best_yolo['name']} at {best_yolo['res']}px")
    
    if avg_enh > avg_raw * 1.5:
        print("  => Recommendation: Enhancement is heavy. Use only for small crops (height < 40px).")
    else:
        print("  => Recommendation: Enhancement is viable for better accuracy.")

if __name__ == "__main__":
    benchmark()
