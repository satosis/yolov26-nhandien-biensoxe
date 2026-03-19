import os
import onnx
from onnxruntime.quantization import quantize_dynamic, QuantType

def compress_model():
    input_model = "models/yolo26_plate_people_door.onnx"
    output_model = "models/yolo26_plate_people_door_int8.onnx"
    
    if not os.path.exists(input_model):
        print(f"[-] Base ONNX model not found at {input_model}. Please run optimize_models.py first.")
        return

    print(f"[*] Starting INT8 Dynamic Quantization: {input_model} -> {output_model}")
    try:
        # Quantize the model
        quantize_dynamic(
            model_input=input_model,
            model_output=output_model,
            weight_type=QuantType.QUInt8
        )
        
        orig_size = os.path.getsize(input_model) / (1024 * 1024)
        new_size = os.path.getsize(output_model) / (1024 * 1024)
        
        print(f"[+] Quantization successful!")
        print(f"  - Original Size: {orig_size:.2f} MB")
        print(f"  - Quantized Size: {new_size:.2f} MB")
        print(f"  - Reduction: {((orig_size - new_size) / orig_size) * 100:.1f}%")
        
    except Exception as e:
        print(f"[-] Quantization failed: {e}")

if __name__ == "__main__":
    compress_model()
