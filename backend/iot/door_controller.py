import time
import logging
import threading
import requests
import tinytuya
from urllib.parse import urlparse

from backend.iot.hunonic_handler import HunonicHandler

logger = logging.getLogger("door_controller")


def _safe_relay_url(url: str) -> bool:
    p = urlparse(url if "://" in url else f"http://{url}")
    host = (p.hostname or "").lower()
    return p.scheme in ("http", "https") and bool(host) and not (
        host.startswith("169.254.") or host == "metadata" or host.endswith(".internal")
    )


class DoorController:
    def __init__(self):
        self.RELAY_TYPE = "tuya_local"
        
        self.RELAY_GPIO_PIN = 11
        
        self.RELAY_HTTP_URL = ""

        from backend.services.database import DatabaseManager
        from backend.services.settings_service import get_app_setting
        db = DatabaseManager()
        self.TUYA_DEVICE_ID = get_app_setting(db, "TUYA_DEVICE_ID", "")
        self.TUYA_LOCAL_KEY = get_app_setting(db, "TUYA_LOCAL_KEY", "")
        self.TUYA_IP = get_app_setting(db, "TUYA_IP", "")
        
        self.door_state_lock = threading.Lock()
        self.door_state = "closed"

    def control_door(self, action: str, relay_type: str = None, relay_config: dict = None) -> bool:
        rtype = relay_type or self.RELAY_TYPE
        rconfig = relay_config or {}
        
        logger.info("Controlling door: %s (Type: %s)", action, rtype)

        try:
            if rtype == "gpio":
                import OPi.GPIO as GPIO

                pin = rconfig.get("pin") or self.RELAY_GPIO_PIN
                GPIO.setmode(GPIO.BOARD) 
                GPIO.setup(pin, GPIO.OUT)
                
                GPIO.output(pin, GPIO.HIGH)
                time.sleep(0.5)
                GPIO.output(pin, GPIO.LOW)
                
                GPIO.cleanup()
                return True

            elif rtype == "tasmota":
                url = rconfig.get("ip") or self.RELAY_HTTP_URL
                if url and _safe_relay_url(url):
                    requests.get(f"{url}/cm?cmnd=Power%20TOGGLE", timeout=5)
                    return True
                elif url:
                    logger.warning("Refused unsafe tasmota relay URL: %s", url)

            elif rtype == "http":
                url = rconfig.get("ip") or self.RELAY_HTTP_URL
                if url and _safe_relay_url(url):
                    requests.get(url, timeout=5)
                    logger.info(f"Sent HTTP request to {url}")
                    return True
                elif url:
                    logger.warning("Refused unsafe http relay URL: %s", url)

            elif rtype == "tuya_local":
                dev_id = rconfig.get("device_id") or self.TUYA_DEVICE_ID
                loc_key = rconfig.get("local_key") or self.TUYA_LOCAL_KEY
                ip = rconfig.get("ip") or self.TUYA_IP or "Auto"
                
                if not dev_id or not loc_key:
                    logger.error("tuya_local requires device_id and local_key")
                    return False

                device = tinytuya.OutletDevice(dev_id=dev_id, address=ip, local_key=loc_key)
                device.set_version(3.3)
                status = device.status()
                current = status.get("dps", {}).get("1", False)
                device.set_value("1", not current)
                logger.info("tuya_local toggle OK — DPS '1': %s -> %s", current, not current)
                return True

            elif rtype == "hunonic_cloud":
                phone = rconfig.get("phone")
                password = rconfig.get("password")
                device_id = rconfig.get("device_id")
                
                if not phone or not password or not device_id:
                    logger.error("hunonic_cloud requires phone, password, and device_id in config")
                    return False
                
                handler = HunonicHandler(phone, password)
                return handler.send_command(device_id, action.lower())

        except Exception as e:
            logger.error(f"Error controlling door ({rtype}): {str(e)}", exc_info=True)
            return False

        if not relay_type:
            with self.door_state_lock:
                if action == "OPEN":
                    self.door_state = "open"
                elif action == "CLOSE":
                    self.door_state = "closed"
        else:
            logger.warning("Door control '%s' for relay_type '%s' did not execute — check config", action, relay_type)

        return False
