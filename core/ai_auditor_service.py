"""
AI Auditor Service — Sử dụng Gemini Vision (google-genai SDK) để kiểm định biển số YOLO
"""
import re
import os


def _normalize_plate(plate: str) -> str:
    """Chuẩn hóa biển số về chữ hoa, bỏ dấu cách và ký tự đặc biệt."""
    if not plate:
        return ""
    return re.sub(r'[\s\-\.]', '', plate.upper().strip())


class AIAuditorService:
    def __init__(self, api_key: str):
        from google import genai
        self._client = genai.Client(api_key=api_key)
        self._model = "gemini-2.0-flash"

    def verify_plate(self, image_path: str, yolo_plate: str) -> dict:
        """
        Gửi ảnh lên Gemini để đọc biển số.
        Returns:
            {
                "gemini_result": str,
                "yolo_plate": str,
                "is_mismatch": bool,
                "error": str or None
            }
        """
        import PIL.Image
        from google.genai import types as genai_types

        if not image_path or not os.path.exists(image_path):
            return {
                "gemini_result": None,
                "yolo_plate": yolo_plate,
                "is_mismatch": False,
                "error": f"Image not found: {image_path}"
            }

        try:
            img = PIL.Image.open(image_path)
            prompt = (
                "You are a Vietnamese license plate OCR expert. "
                "Look at this image and read the license plate number. "
                "Reply with ONLY the plate number (letters and digits), no spaces, no explanation. "
                "If there is no plate visible, reply 'NONE'."
            )
            response = self._client.models.generate_content(
                model=self._model,
                contents=[prompt, img]
            )
            gemini_text = response.text.strip().upper() if response.text else "NONE"
            gemini_norm = _normalize_plate(gemini_text)
            yolo_norm = _normalize_plate(yolo_plate)
            is_mismatch = (
                gemini_text != "NONE"
                and gemini_norm != yolo_norm
                and len(gemini_norm) > 3
            )
            return {
                "gemini_result": gemini_text,
                "yolo_plate": yolo_plate,
                "is_mismatch": is_mismatch,
                "error": None
            }
        except Exception as e:
            return {
                "gemini_result": None,
                "yolo_plate": yolo_plate,
                "is_mismatch": False,
                "error": str(e)
            }
