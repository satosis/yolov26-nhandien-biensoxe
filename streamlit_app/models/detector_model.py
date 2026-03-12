import os
import cv2
import numpy as np
import streamlit as st
from ultralytics import YOLO
from pathlib import Path

# Constants from original streamlit_qa.py
_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "models")
_CUSTOM_MODEL = os.path.join(_MODELS_DIR, "custom_detector.pt")
_DEFAULT_MODEL = os.path.join(_MODELS_DIR, "bien_so_xe.pt")
MODEL_PATH = _CUSTOM_MODEL if os.path.exists(_CUSTOM_MODEL) else _DEFAULT_MODEL

@st.cache_resource(show_spinner="Đang tải model YOLO…")
def load_yolo_model():
    print(f"[INFO] Loading YOLO model: {MODEL_PATH}")
    return YOLO(MODEL_PATH)


@st.cache_resource(show_spinner="Đang tải PaddleOCR…")
def load_ocr():
    try:
        from paddleocr import PaddleOCR
        return PaddleOCR(use_angle_cls=True, lang="en", show_log=False, use_gpu=False)
    except ImportError:
        return None

@st.cache_resource(show_spinner="Đang tải InsightFace…")
def load_face_app():
    try:
        from insightface.app import FaceAnalysis
        app = FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(320, 320))
        return app
    except ImportError:
        return None

import bcrypt

def verify_password(plain_password, hashed_password):
    try:
        if isinstance(plain_password, str):
            plain_password = plain_password.encode('utf-8')
        if isinstance(hashed_password, str):
            hashed_password = hashed_password.encode('utf-8')
        return bcrypt.checkpw(plain_password, hashed_password)
    except Exception as e:
        st.error(f"Auth error: {e}")
        return False

def get_password_hash(password):
    if isinstance(password, str):
        password = password.encode('utf-8')
    return bcrypt.hashpw(password, bcrypt.gensalt()).decode('utf-8')

def get_db():
    try:
        from core.database import DatabaseManager
        return DatabaseManager()
    except Exception:
        return None

def get_asset_registry():
    try:
        from core.asset_registry import AssetRegistry
        # We don't need DB_PATH anymore as DatabaseManager uses env vars, 
        # but AssetRegistry might still expect it if not refactored yet.
        # However, our recent refactor changed DatabaseManager to use env.
        # For now, passing None if it's not strictly needed or env-based.
        return AssetRegistry(None) 
    except Exception:
        return None
