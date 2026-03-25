import sys
import os
import cv2

# Mocking the logic from grabber.py to test it without running the whole app
def get_backend_for_url(url):
    is_num = str(url).isdigit()
    
    if is_num:
        source = int(url)
        if sys.platform == "darwin":
            backend = cv2.CAP_AVFOUNDATION
        else:
            backend = cv2.CAP_ANY
    else:
        source = url
        protocols = ["rtsp://", "rtmp://", "http://", "https://"]
        if any(url.lower().startswith(p) for p in protocols):
            backend = cv2.CAP_FFMPEG
        else:
            backend = cv2.CAP_ANY
    
    return source, backend

def test_logic():
    test_cases = [
        ("0", "darwin", cv2.CAP_AVFOUNDATION),
        ("0", "linux", cv2.CAP_ANY),
        ("rtsp://admin:pass@192.168.1.1", "darwin", cv2.CAP_FFMPEG),
        ("http://stream.mjpg", "linux", cv2.CAP_FFMPEG),
        ("local_video.mp4", "darwin", cv2.CAP_ANY),
    ]
    
    print(f"Running platform: {sys.platform}")
    
    for url, plat, expected_backend in test_cases:
        # We override sys.platform for testing logic if needed, 
        # but here we just check if it matches the current platform's expected behavior
        if plat == sys.platform or plat == "any":
            src, bnk = get_backend_for_url(url)
            print(f"URL: {url:30} -> Source: {src!r:10} Backend: {bnk}")
            # Note: We don't actually open the capture here to avoid hardware dependencies in tests
            # unless we specifically want to test the warning suppression.

if __name__ == "__main__":
    test_logic()
    
    # Optional: Actual test if device 0 exists
    print("\nAttempting to open source 0 with AVFoundation (if on Mac)...")
    src, bnk = get_backend_for_url("0")
    cap = cv2.VideoCapture(src, bnk)
    if cap.isOpened():
        print("✅ Successfully opened source 0")
        cap.release()
    else:
        print("ℹ️ Source 0 not available (expected if no camera connected)")
