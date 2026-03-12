import json
import os
import logging

class SettingsManager:
    def __init__(self, settings_path):
        self.settings_path = settings_path
        self.defaults = {
            "PROCESS_WIDTH": 960,
            "STREAM_WIDTH": 960,
            "STREAM_FPS": 8,
            "STREAM_JPEG_QUALITY": 68,
            "GENERAL_DETECT_CONF": 0.35,
            "GENERAL_DETECT_IMGSZ": 640,
            "PLATE_DETECT_EVERY_N_FRAMES": 3,
            "LINE_Y_RATIO": 0.62,
            "SIGNAL_LOSS_TIMEOUT": 30
        }
        self.settings = self.load_settings()

    def load_settings(self):
        if not os.path.exists(self.settings_path):
            self.save_settings(self.defaults)
            return self.defaults.copy()
        
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Merge defaults to ensure all keys exist
                settings = self.defaults.copy()
                settings.update(data)
                return settings
        except Exception as e:
            logging.error(f"Error loading settings: {e}")
            return self.defaults.copy()

    def save_settings(self, new_settings):
        try:
            os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(new_settings, f, indent=4)
            self.settings = new_settings
            return True
        except Exception as e:
            logging.error(f"Error saving settings: {e}")
            return False

    def get(self, key):
        return self.settings.get(key, self.defaults.get(key))
