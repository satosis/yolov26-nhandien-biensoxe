import cv2
import numpy as np
import logging

logger = logging.getLogger("texture_utils")

def check_plate_authenticity(crop) -> tuple[bool, float, str]:
    """
    Analyze if the plate is likely real (metallic/reflective) or fake (printed paper/taped).
    Returns (is_suspicious, confidence, reason)
    """
    if crop is None or crop.size == 0:
        return False, 0.0, "empty"

    # Convert to gray
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    
    # 1. Laplacian Variance (Sharpness check)
    # Real plates have a specific blur/gradient on characters due to metallic reflection.
    # Printed paper often has "too sharp" edges or "too blurry" low-res artifacts.
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    
    # 2. Intensity Distribution (Contrast check)
    # Real metallic plates have specular highlights (high peak in histogram).
    # Paper has a flatter, more even distribution.
    _, max_val, _, _ = cv2.minMaxLoc(gray)
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
    high_intensity_pixels = np.sum(hist[200:]) / np.sum(hist)
    
    # 3. Local Phase Congruency (Simplified as Edge Density)
    edges = cv2.Canny(gray, 100, 200)
    edge_density = np.sum(edges) / (gray.shape[0] * gray.shape[1] * 255)

    # Heuristic Logic
    suspicious = False
    reasons = []
    confidence = 0.0

    # Case A: Too Sharp (Laser printer on paper)
    if laplacian_var > 1500:
        suspicious = True
        reasons.append("abnormal_sharpness")
        confidence += 0.4
        
    # Case B: No specular highlights (Flat paper/Taped)
    if high_intensity_pixels < 0.02:
        suspicious = True
        reasons.append("no_reflection")
        confidence += 0.3

    # Case C: Abnormal Edge Density
    if edge_density > 0.35:
        suspicious = True
        reasons.append("high_noise_texture")
        confidence += 0.2

    reason_str = "|".join(reasons) if reasons else "normal"
    return suspicious, min(1.0, confidence), reason_str

def get_debug_info(crop):
    """Returns raw metrics for tuning."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
    high_intensity_pixels = np.sum(hist[200:]) / np.sum(hist)
    edges = cv2.Canny(gray, 100, 200)
    edge_density = np.sum(edges) / (gray.shape[0] * gray.shape[1] * 255)
    
    return {
        "laplacian_var": laplacian_var,
        "high_intensity_ratio": high_intensity_pixels,
        "edge_density": edge_density
    }
