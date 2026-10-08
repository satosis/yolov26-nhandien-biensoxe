import requests
import json
import logging
import time
from typing import Optional, List, Dict

try:
    import paho.mqtt.client as mqtt
except ImportError:
    # We will handle the fallback or installation in the runtime if needed
    mqtt = None

logger = logging.getLogger("hunonic_handler")
class HunonicHandler:
    """Handles authentication and control for Hunonic Cloud devices."""
    
    API_BASE = "http://103.221.223.62"
    MQTT_HOST = "103.221.223.62"
    MQTT_PORT = 1883
    
    def __init__(self, phone: str, password: str):
        self.phone = phone
        self.password = password
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.last_login: float = 0
        self.token_expiry: int = 3600 * 24 # Assume 24h
        
    def login(self) -> bool:
        """Authenticate with Hunonic Cloud and retrieve access token."""
        url = f"{self.API_BASE}/auth/login"
        payload = {
            "phone": self.phone,
            "password": self.password
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                # Based on reverse engineered payloads, adjust keys if necessary
                self.token = data.get("access_token") or data.get("token")
                self.user_id = str(data.get("user_id") or data.get("id"))
                self.last_login = time.time()
                logger.info("Hunonic login successful for user: %s", self.phone)
                return True
            else:
                logger.error("Hunonic login failed: %s - %s", response.status_code, response.text)
                return False
        except Exception as e:
            logger.error("Hunonic login exception: %s", str(e), exc_info=True)
            return False

    def get_devices(self) -> List[Dict]:
        """Fetch list of devices registered to this account."""
        if not self.token and not self.login():
            return []
            
        url = f"{self.API_BASE}/devices"
        headers = {"Authorization": f"Bearer {self.token}"}
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json().get("devices", [])
            else:
                logger.error("Failed to fetch Hunonic devices: %s", response.status_code)
                return []
        except Exception as e:
            logger.error("Hunonic get_devices exception: %s", str(e))
            return []

    def send_command(self, device_id: str, cmd: str) -> bool:
        """Send a control command (open, close, stop) via MQTT."""
        if mqtt is None:
            logger.error("paho-mqtt not installed — cannot send Hunonic command")
            return False
            
        if not self.token and not self.login():
            return False

        # Commands are typically 'open', 'close', 'stop'
        payload = json.dumps({
            "cmd": cmd.lower(),
            "data": 1
        })
        
        topic = f"hunonic/device/{device_id}/command"
        
        try:
            client = mqtt.Client(client_id=f"ParkingAI_{self.phone}_{int(time.time())}")
            client.username_pw_set(self.phone, self.token)
            
            # Connect and publish synchronously for simplicity in this bridge
            client.connect(self.MQTT_HOST, self.MQTT_PORT, 60)
            client.loop_start()
            
            result = client.publish(topic, payload, qos=1)
            result.wait_for_publish()
            
            client.loop_stop()
            client.disconnect()
            
            logger.info("Sent Hunonic command '%s' to device %s", cmd, device_id)
            return True
        except Exception as e:
            logger.error("Hunonic MQTT command exception for device %s: %s", device_id, str(e), exc_info=True)
            return False

# Global singleton or cache can be implemented later if needed
