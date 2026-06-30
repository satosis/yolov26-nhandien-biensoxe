import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import os

# Try to find a nice font
FONT_PATHS = [
    "/System/Library/Fonts/Cache/Heiti.ttc", # Mac
    "/System/Library/Fonts/SFNS.ttf", # Mac
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", # Linux
    "venv/lib/python3.11/site-packages/matplotlib/mpl-data/fonts/ttf/DejaVuSans-Bold.ttf", # Matplotlib bundled
]

_font_cache = {}

def get_font(size):
    if size in _font_cache:
        return _font_cache[size]
    
    font = None
    for path in FONT_PATHS:
        if os.path.exists(path):
            font = ImageFont.truetype(path, size)
            break

    if font is None:
        font = ImageFont.load_default()
    
    _font_cache[size] = font
    return font

def draw_text_premium(img, text, position, font_size=20, color=(255, 255, 255), bgcolor=(0, 0, 0, 150)):
    """Draw text with a premium look using PIL. Extremely optimized with ROI."""
    font = get_font(font_size)
    
    # Text size estimation
    dummy_img = Image.new("RGBA", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)
    left, top, right, bottom = dummy_draw.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    
    # Calculate padding and actual bounding box
    pad = 6
    box_w, box_h = tw + pad * 2, th + pad * 2
    px, py = max(0, position[0] - pad), max(0, position[1] - pad)
    
    # Prevent out of bounds
    img_h, img_w = img.shape[:2]
    if px + box_w > img_w or py + box_h > img_h:
        # Fallback to simple CV2 if clipping off-screen for safety
        cv2.putText(img, text, position, cv2.FONT_HERSHEY_SIMPLEX, font_size*0.03, color, 2)
        return img
    
    # 1. Create a tiny RGBA PIL Image exactly the size of the background box
    text_img_pil = Image.new("RGBA", (box_w, box_h), color=bgcolor)
    draw = ImageDraw.Draw(text_img_pil)
    
    # 2. Draw Text onto the tiny image
    draw.text((pad, pad), text, font=font, fill=(color[2], color[1], color[0], 255))
    
    # 3. Convert tiny image back to Numpy
    text_overlay_bgr4 = cv2.cvtColor(np.array(text_img_pil), cv2.COLOR_RGBA2BGRA)
    overlay_bgr = text_overlay_bgr4[:, :, :3]
    alpha = text_overlay_bgr4[:, :, 3:] / 255.0  # Normalized Alpha channel
    
    # 4. Extract ROI from original image and blend
    roi = img[py:py+box_h, px:px+box_w]
    blended = (overlay_bgr * alpha + roi * (1.0 - alpha)).astype(np.uint8)
    
    # 5. Patch back
    img[py:py+box_h, px:px+box_w] = blended
    return img

def draw_minimal_rect(img, pt1, pt2, color, thickness=1, label=None):
    """Draw a thin minimalist bounding box and label."""
    x1, y1 = pt1
    x2, y2 = pt2
    
    # Main rectangle (very thin)
    cv2.rectangle(img, (x1, y1), (x2, y2), color, thickness)
    
    if label:
        font_size = 12
        font = get_font(font_size)
        
        # Get text size
        dummy_img = Image.new("RGBA", (1, 1))
        dummy_draw = ImageDraw.Draw(dummy_img)
        left, top, right, bottom = dummy_draw.textbbox((0, 0), label, font=font)
        tw, th = right - left, bottom - top
        
        box_w, box_h = tw + 6, th + 4
        px = max(0, x1)
        py = max(0, y1 - th - 4)
        if py == 0: py = y1 + 2
        
        img_h, img_w = img.shape[:2]
        if px + box_w > img_w or py + box_h > img_h:
            cv2.putText(img, label, (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, thickness)
            return

        # Tiny Canvas
        text_img = Image.new("RGBA", (box_w, box_h), color=(color[0], color[1], color[2], 220))
        draw = ImageDraw.Draw(text_img)
        draw.text((3, 2), label, font=font, fill=(255, 255, 255, 255))
        
        # Alpha Blend
        text_overlay_bgr4 = cv2.cvtColor(np.array(text_img), cv2.COLOR_RGBA2BGRA)
        overlay_bgr = text_overlay_bgr4[:, :, :3]
        alpha = text_overlay_bgr4[:, :, 3:] / 255.0
        
        roi = img[py:py+box_h, px:px+box_w]
        blended = (overlay_bgr * alpha + roi * (1.0 - alpha)).astype(np.uint8)
        img[py:py+box_h, px:px+box_w] = blended

def draw_premium_label(img, x1, y1, x2, y2, label, color):
    """Tech/CCTV style label above bounding box, auto-scaled font."""
    img_h, img_w = img.shape[:2]

    # Thicker border for large images
    thickness = max(2, int(img_h / 500))
    cv2.rectangle(img, (x1, y1), (x2, y2), color, thickness)

    # Scale font: ~2% of image height, min 16, no max cap
    font_size = max(16, int(img_h * 0.02))
    font = get_font(font_size)

    dummy_img = Image.new("RGBA", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)
    left, top, right, bottom = dummy_draw.textbbox((0, 0), label, font=font)
    tw, th = right - left, bottom - top

    pad_x, pad_y = 10, 6
    box_w, box_h = tw + pad_x * 2, th + pad_y * 2
    px = max(0, min(x1, img_w - box_w))
    # Place label ABOVE the bounding box; fallback below if no room
    py = y1 - box_h - 4
    if py < 0:
        py = y2 + 4
    if py + box_h > img_h:
        py = max(0, y1 - box_h)

    # Clamp to image bounds
    if px + box_w > img_w:
        px = max(0, img_w - box_w)
    if py + box_h > img_h or px + box_w > img_w:
        # Fallback: simple cv2 text
        font_scale = max(0.5, img_h / 1500)
        cv2.putText(img, label, (x1, max(y1 - 10, 20)), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, max(1, thickness))
        return

    # Background with alpha
    bg_color = (color[0], color[1], color[2], 200)
    text_img = Image.new("RGBA", (box_w, box_h), color=bg_color)
    draw = ImageDraw.Draw(text_img)
    draw.text((pad_x, pad_y), label, font=font, fill=(255, 255, 255, 255))

    text_overlay_bgr4 = cv2.cvtColor(np.array(text_img), cv2.COLOR_RGBA2BGRA)
    overlay_bgr = text_overlay_bgr4[:, :, :3]
    alpha_ch = text_overlay_bgr4[:, :, 3:] / 255.0

    roi = img[py:py+box_h, px:px+box_w]
    if roi.shape[0] != box_h or roi.shape[1] != box_w:
        font_scale = max(0.5, img_h / 1500)
        cv2.putText(img, label, (x1, max(y1 - 10, 20)), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, max(1, thickness))
        return
    blended = (overlay_bgr * alpha_ch + roi * (1.0 - alpha_ch)).astype(np.uint8)
    img[py:py+box_h, px:px+box_w] = blended
