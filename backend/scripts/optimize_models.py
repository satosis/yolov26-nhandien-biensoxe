import os
import sys
from ultralytics import YOLO
import ultralytics.utils.loss

# Fix for E2ELoss error when loading YOLOv10 models
if not hasattr(ultralytics.utils.loss, "E2ELoss"):
    class E2ELoss:
        pass
    ultralytics.utils.loss.E2ELoss = E2ELoss

def optimize_yolo_model(model_path):
    """
    Optimizes a YOLO model by exporting it to ONNX format with FP16 precision.
    This is ideal for ARM devices like Orange Pi to reduce memory usage and increase speed.
    """
    if not os.path.exists(model_path):
        print(f"[-] Error: Model file not found at {model_path}")
        return

    print(f"[*] Loading model: {model_path}")
    model = YOLO(model_path)
    
    # Define output path
    base_name = os.path.splitext(model_path)[0]
    onnx_path = base_name + ".onnx"
    
    print(f"[*] Optimizing to ONNX (FP16)...")
    try:
        # Exporting to ONNX with half=True (FP16)
        # opset=12 is widely compatible
        path = model.export(format="onnx", half=True, opset=12, simplify=True)
        print(f"[+] Optimization successful! Model saved at: {path}")
    except Exception as e:
        print(f"[-] Optimization failed: {e}")

if __name__ == "__main__":
    # Get base directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model_file = os.path.join(base_dir, "models", "yolo26_plate_people_door.pt")
    
    optimize_yolo_model(model_file)
