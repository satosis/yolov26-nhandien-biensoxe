import os
import time
import logging
import threading
import requests
import tinytuya
import importlib.util

# OPi.GPIO is only available/functional on Linux/Orange Pi systems
GPIO = None
try:
    import OPi.GPIO as GPIO
except (ImportError, RuntimeError, AttributeError):
    # This will fail on macOS because of 'select.EPOLLIN' absence
    pass

from backend.core import config as cfg
from backend.core.hunonic_handler import HunonicHandler

logger = logging.getLogger("door_controller")

class DoorController:
    def __init__(self):
        # Relay control for garage door
        self.RELAY_TYPE = "tuya_local"  # Options: gpio, tasmota, http, tuya_local
        
        # GPIO Config
        self.RELAY_GPIO_PIN = 11  # Orange Pi 4 Pro (Physical Pin 11)
        
        # Tasmota/HTTP Config
        self.RELAY_HTTP_URL = ""

        # Tuya Local Config (Get these from Tuya IoT Platform or tinytuya scan)
        self.TUYA_DEVICE_ID = os.environ.get("TUYA_DEVICE_ID", "")
        self.TUYA_LOCAL_KEY = os.environ.get("TUYA_LOCAL_KEY", "")
        self.TUYA_IP = os.environ.get("TUYA_IP", "")  # Optional but recommended for speed
        
        self.door_state_lock = threading.Lock()
        self.door_state = "closed" # open, closed, opening, closing

    def control_door(self, action: str, relay_type: str = None, relay_config: dict = None) -> bool:
        """Control garage door relay: OPEN, CLOSE, STOP"""
        rtype = relay_type or self.RELAY_TYPE
        rconfig = relay_config or {}
        
        logger.info("Controlling door: %s (Type: %s)", action, rtype)

        try:
            if rtype == "gpio":
                if GPIO is None:
                    logger.error("OPi.GPIO not installed — cannot control door via gpio")
                    return False
                
                # Setup GPIO
                pin = rconfig.get("pin") or self.RELAY_GPIO_PIN
                GPIO.setmode(GPIO.BOARD) 
                GPIO.setup(pin, GPIO.OUT)
                
                # Pulse logic for garage door (Toggle)
                GPIO.output(pin, GPIO.HIGH)
                time.sleep(0.5)  # 0.5s pulse
                GPIO.output(pin, GPIO.LOW)
                
                GPIO.cleanup()
                return True

            elif rtype == "tasmota":
                url = rconfig.get("ip") or self.RELAY_HTTP_URL
                if url:
                    # Assumes simple toggle for garage door
                    requests.get(f"{url}/cm?cmnd=Power%20TOGGLE", timeout=5)
                    return True

            elif rtype == "http":
                url = rconfig.get("ip") or self.RELAY_HTTP_URL
                if url:
                    # Generic HTTP GET request
                    requests.get(url, timeout=5)
                    logger.info(f"Sent HTTP request to {url}")
                    return True

            elif rtype == "tuya_local":
                # Use provided config or fallback to defaults
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
                # HunonicHandler takes lower case commands like 'open', 'close', 'stop'
                return handler.send_command(device_id, action.lower())

        except Exception as e:
            logger.error(f"Error controlling door ({rtype}): {str(e)}", exc_info=True)
            return False

        # Simulating state change for UI feedback (legacy logic for main garage door)
        if not relay_type:
            with self.door_state_lock:
                if action == "OPEN":
                    self.door_state = "open"
                elif action == "CLOSE":
                    self.door_state = "closed"
        
        return False
