
import os
import time
import logging
import threading
import requests

try:
    import tinytuya
    _TINYTUYA_AVAILABLE = True
except ImportError:
    _TINYTUYA_AVAILABLE = False
    logging.getLogger("door_controller").warning(
        "tinytuya not installed — tuya_local relay type unavailable. Run: pip install tinytuya"
    )

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

    def control_door(self, action: str) -> None:
        """Control garage door relay: OPEN, CLOSE, STOP"""
        logger.info("Controlling door: %s (Type: %s)", action, self.RELAY_TYPE)

        if self.RELAY_TYPE == "gpio":
            try:
                import OPi.GPIO as GPIO
                # Setup GPIO (BOARD or BCM - Orange Pi usually BOARD or SUNXI)
                GPIO.setmode(GPIO.BOARD) 
                GPIO.setup(self.RELAY_GPIO_PIN, GPIO.OUT)
                
                # Pulse logic for garage door (Toggle)
                GPIO.output(self.RELAY_GPIO_PIN, GPIO.HIGH)
                time.sleep(0.5)  # 0.5s pulse
                GPIO.output(self.RELAY_GPIO_PIN, GPIO.LOW)
                
                GPIO.cleanup()
            except ImportError:
                logger.error("OPi.GPIO not installed. Run: pip install OPi.GPIO")
            except Exception as exc:
                logger.error("GPIO control failed: %s", exc)

        elif self.RELAY_TYPE == "tasmota":
            if self.RELAY_HTTP_URL:
                try:
                    # Assumes simple toggle for garage door
                    requests.get(f"{self.RELAY_HTTP_URL}/cm?cmnd=Power%20TOGGLE", timeout=2)
                except Exception as exc:
                    logger.error("Tasmota control failed: %s", exc)

        elif self.RELAY_TYPE == "http":
            if self.RELAY_HTTP_URL:
                try:
                    # Generic HTTP GET request
                    # Example: http://192.168.1.50/control?cmd=open
                    # You can append action to URL if needed, e.g. f"{self.RELAY_HTTP_URL}/{action}"
                    # For now, assumes URL triggers the action directly (toggle)
                    requests.get(self.RELAY_HTTP_URL, timeout=2)
                    logger.info(f"Sent HTTP request to {self.RELAY_HTTP_URL}")
                except Exception as exc:
                    logger.error("HTTP control failed: %s", exc)

        elif self.RELAY_TYPE == "tuya_local":
            if not _TINYTUYA_AVAILABLE:
                logger.error("tinytuya not installed — cannot control door via tuya_local")
            elif not self.TUYA_DEVICE_ID or not self.TUYA_LOCAL_KEY:
                logger.error(
                    "tuya_local requires TUYA_DEVICE_ID and TUYA_LOCAL_KEY env vars"
                )
            else:
                try:
                    device = tinytuya.OutletDevice(
                        dev_id=self.TUYA_DEVICE_ID,
                        address=self.TUYA_IP or "Auto",
                        local_key=self.TUYA_LOCAL_KEY,
                    )
                    device.set_version(3.3)
                    # Read current DPS "1" state then toggle
                    status = device.status()
                    current = status.get("dps", {}).get("1", False)
                    device.set_value("1", not current)
                    logger.info(
                        "tuya_local toggle OK — DPS '1': %s -> %s (action=%s)",
                        current, not current, action,
                    )
                except Exception as exc:
                    logger.error("tuya_local control failed: %s", exc)

        # Simulating state change for UI feedback
        with self.door_state_lock:
            if action == "OPEN":
                self.door_state = "open"
            elif action == "CLOSE":
                self.door_state = "closed"
