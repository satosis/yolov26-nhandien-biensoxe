import onnx
import onnx.compose as compose
from onnx import version_converter
import os

def merge_everything_to_one():
    """
    Consolidate Plate, Face, and OCR models into a single multi-input .onnx file.
    Overwrites the original yolo26_plate_people_door_int8.onnx.
    """
    
    # Paths
    plate_model = "/Users/mac/Documents/yolov26-nhandien-biensoxe/backend/models/yolo26_plate_people_door_int8.onnx.bak"
    face_det = "/Users/mac/Documents/yolov26-nhandien-biensoxe/backend/models/insightface/models/buffalo_sc/det_500m.onnx"
    face_rec = "/Users/mac/Documents/yolov26-nhandien-biensoxe/backend/models/insightface/models/buffalo_sc/w600k_mbf.onnx"
    ocr_det = "/opt/homebrew/lib/python3.11/site-packages/rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx"
    ocr_rec = "/opt/homebrew/lib/python3.11/site-packages/rapidocr_onnxruntime/models/ch_PP-OCRv4_rec_infer.onnx"
    
    target_path = "/Users/mac/Documents/yolov26-nhandien-biensoxe/backend/models/yolo26_plate_people_door_int8.onnx"
    
    target_opset = 12
    target_ir = 8
    
    print("--- Merging ALL models into yolo26_plate_people_door_int8.onnx ---")
    
    def prep_model(path, prefix):
        print(f"Processing: {os.path.basename(path)}")
        m = onnx.load(path)
        
        # Opset upgrade
        if m.opset_import[0].version < target_opset:
            try:
                m = version_converter.convert_version(m, target_opset)
            except:
                m.opset_import[0].version = target_opset
                
        # IR upgrade
        if m.ir_version < target_ir:
            m.ir_version = target_ir

        return compose.add_prefix(m, prefix=prefix)
    
    try:
        # Load sub-components
        m_plate = prep_model(plate_model, "plate_")
        m_face_det = prep_model(face_det, "face_det_")
        m_face_rec = prep_model(face_rec, "face_rec_")
        m_ocr_det = prep_model(ocr_det, "ocr_det_")
        m_ocr_rec = prep_model(ocr_rec, "ocr_rec_")
        
        # Stitch
        print("Stitching graphs...")
        merged = m_plate
        merged = compose.merge_models(merged, m_face_det, io_map=[])
        merged = compose.merge_models(merged, m_face_rec, io_map=[])
        merged = compose.merge_models(merged, m_ocr_det, io_map=[])
        merged = compose.merge_models(merged, m_ocr_rec, io_map=[])
        
        # Save over original
        print(f"Overwriting original: {target_path}")
        onnx.save(merged, target_path)
        print("SUCCESS: Unified 'all-in-one' model created!")
        
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    merge_everything_to_one()
