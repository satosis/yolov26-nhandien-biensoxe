"""Prepare uploaded company signatures and stamps for document overlays."""

from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from PIL.PngImagePlugin import PngInfo


MAX_IMAGE_PIXELS = 12_000_000
MAX_OUTPUT_SIDE = 2400
SIGNATURE_PRINT_WIDTH_MM = 74.0
STAMP_DIAMETER_MM = 36.0
PRINT_WIDTH_KEY = "company_print_width_mm"


def company_image_print_width_mm(path: str | Path, default_mm: float = SIGNATURE_PRINT_WIDTH_MM) -> float:
    """Read the intended physical width of a saved company image."""
    try:
        with Image.open(path) as image:
            value = float(image.info.get(PRINT_WIDTH_KEY, default_mm))
            return value if 20 <= value <= 100 else default_mm
    except (OSError, TypeError, ValueError):
        return default_mm


def stamp_image_print_width_mm(image: Image.Image) -> float:
    """Size the canvas so the visible circular stamp prints at 36 mm."""
    alpha = image.getchannel("A")
    ring_bounds = alpha.point(lambda value: 255 if value > 64 else 0).getbbox()
    if not ring_bounds:
        raise ValueError("Ảnh không có nét con dấu rõ để xác định đường kính.")
    left, top, right, bottom = ring_bounds
    diameter_px = min(right - left, bottom - top)
    return STAMP_DIAMETER_MM * image.width / diameter_px


def transparent_company_image(content: bytes, *, stamp: bool = False) -> bytes:
    """Return cropped transparent artwork with its intended print size embedded."""
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                raise ValueError("Chọn ảnh PNG, JPG, WebP hoặc GIF.")
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("Ảnh quá lớn; chọn ảnh dưới 12 megapixel.")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((MAX_OUTPUT_SIDE, MAX_OUTPUT_SIDE), Image.Resampling.LANCZOS)
            image = image.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Không đọc được ảnh chữ ký hoặc con dấu.") from exc

    pixels = np.asarray(image, dtype=np.uint8).copy()
    original_alpha = pixels[:, :, 3]

    # Transparent source artwork is already suitable for placing on documents.
    if not np.any(original_alpha < 250):
        rgb = pixels[:, :, :3].astype(np.float32)
        height, width = original_alpha.shape
        edge = max(1, min(height, width) // 20)
        corners = np.concatenate((
            rgb[:edge, :edge].reshape(-1, 3), rgb[:edge, -edge:].reshape(-1, 3),
            rgb[-edge:, :edge].reshape(-1, 3), rgb[-edge:, -edge:].reshape(-1, 3),
        ))
        background = np.median(corners, axis=0)
        if background.min() < 225 or np.ptp(background) > 30:
            raise ValueError("Ảnh chữ ký hoặc con dấu cần nền giấy sáng để tách nền chính xác.")

        # Estimate the ink before it was blended with the paper. This keeps
        # thin black strokes and soft red stamp edges without a white halo.
        ink = np.max(np.maximum(background - rgb, 0), axis=2)
        estimated_alpha = np.clip(ink / background.max(), 0, 1)
        safe_alpha = np.maximum(estimated_alpha[:, :, None], 1 / 255)
        foreground = (rgb - background * (1 - estimated_alpha[:, :, None])) / safe_alpha
        pixels[:, :, :3] = np.clip(foreground, 0, 255).astype(np.uint8)
        pixels[:, :, 3] = np.clip((ink - 10) * 255 / (background.max() - 10), 0, 255).astype(np.uint8)

    visible = Image.fromarray(pixels)
    alpha_bounds = visible.getchannel("A").point(lambda value: 255 if value > 8 else 0).getbbox()
    if not alpha_bounds:
        raise ValueError("Ảnh không có nét chữ ký hoặc con dấu rõ để lưu.")
    padding = max(8, round(min(visible.size) * 0.015))
    left, top, right, bottom = alpha_bounds
    visible = visible.crop((max(0, left - padding), max(0, top - padding),
                            min(visible.width, right + padding), min(visible.height, bottom + padding)))
    print_width_mm = stamp_image_print_width_mm(visible) if stamp else SIGNATURE_PRINT_WIDTH_MM
    output = BytesIO()
    metadata = PngInfo()
    metadata.add_text(PRINT_WIDTH_KEY, str(print_width_mm))
    dpi = visible.width * 25.4 / print_width_mm
    visible.save(output, format="PNG", optimize=True, pnginfo=metadata, dpi=(dpi, dpi))
    return output.getvalue()
