import cv2
import numpy as np

def check_plate_authenticity(crop) -> tuple[bool, float, str]:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
    high_intensity_pixels = np.sum(hist[200:]) / np.sum(hist)
    
    edges = cv2.Canny(gray, 100, 200)
    edge_density = np.sum(edges) / (gray.shape[0] * gray.shape[1] * 255)

    suspicious = False
    reasons = []
    confidence = 0.0

    if laplacian_var > 1500:
        suspicious = True
        reasons.append("abnormal_sharpness")
        confidence += 0.4
        
    if high_intensity_pixels < 0.02:
        suspicious = True
        reasons.append("no_reflection")
        confidence += 0.3

    if edge_density > 0.35:
        suspicious = True
        reasons.append("high_noise_texture")
        confidence += 0.2

    reason_str = "|".join(reasons) if reasons else "normal"
    return suspicious, min(1.0, confidence), reason_str

