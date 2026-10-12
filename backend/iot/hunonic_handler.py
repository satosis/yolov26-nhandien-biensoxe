import requests
import json
import logging
import os
import time
import uuid
from typing import Optional, List, Dict

logger = logging.getLogger("hunonic_handler")
class HunonicHandler:
    
    API_BASE = os.environ.get("HUNONIC_API_BASE", "http://103.221.223.62").rstrip("/")
    MQTT_HOST = "103.221.223.62"
    MQTT_PORT = 1883
    
    def __init__(self, phone: str, password: str):
        self.phone = phone
        self.password = password
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.last_login: float = 0
        self.token_expiry: int = 3600 * 24
        
    def login(self) -> bool:
        url = f"{self.API_BASE}/auth/login"
        payload = {
            "phone": self.phone,
            "password": self.password
        }
        
        request_completed = False
        try:
            response = requests.post(url, json=payload, timeout=10)
            request_completed = True
        finally:
            if not request_completed:
                logger.error("Hunonic login transport failed")
        if not response.ok:
            logger.error("Hunonic login failed with HTTP %s", response.status_code)
        response.raise_for_status()
        json_decoded = False
        try:
            data = response.json()
            json_decoded = True
        finally:
            if not json_decoded:
                logger.error("Hunonic login response was not valid JSON")
        if not isinstance(data, dict):
            logger.error("Hunonic login response is not an object")
            raise ValueError("Hunonic login response must be an object")
        token = data.get("access_token") or data.get("token")
        if not isinstance(token, str) or not token.strip():
            logger.error("Hunonic login response has no valid access token")
            raise ValueError("Hunonic login response is missing a valid access token")
        user_id = data.get("user_id")
        if user_id is None:
            user_id = data.get("id")
        if user_id is not None and (isinstance(user_id, bool) or not isinstance(user_id, (str, int))):
            logger.error("Hunonic login response has an invalid user ID")
            raise ValueError("Hunonic login response has an invalid user ID")
        self.token = token.strip()
        self.user_id = str(user_id) if user_id is not None else None
        self.last_login = time.time()
        logger.info("Hunonic login successful")
        return True

    def get_devices(self) -> List[Dict]:
        if not self.token:
            self.login()
        url = f"{self.API_BASE}/devices"
        headers = {"Authorization": f"Bearer {self.token}"}
        request_completed = False
        try:
            response = requests.get(url, headers=headers, timeout=10)
            request_completed = True
        finally:
            if not request_completed:
                logger.error("Hunonic device-list transport failed")
        if not response.ok:
            logger.error("Hunonic device list failed with HTTP %s", response.status_code)
        response.raise_for_status()
        json_decoded = False
        try:
            data = response.json()
            json_decoded = True
        finally:
            if not json_decoded:
                logger.error("Hunonic device-list response was not valid JSON")
        if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
            logger.error("Hunonic device list response has no devices array")
            raise ValueError("Hunonic devices response must contain a list")
        devices = data["devices"]
        normalized = []
        for device in devices:
            if not isinstance(device, dict):
                logger.error("Hunonic device list contains a non-object entry")
                raise ValueError("Hunonic devices response contains an invalid device")
            device_id = device.get("id")
            if device_id is None:
                device_id = device.get("device_id")
            if device_id is None:
                device_id = device.get("deviceId")
            name = device.get("name")
            if name is None:
                name = device.get("deviceName")
            if name is None:
                name = "Hunonic Device"
            if isinstance(device_id, bool) or not isinstance(device_id, (str, int)) or not str(device_id).strip():
                logger.error("Hunonic device list contains an invalid device ID")
                raise ValueError("Hunonic devices response contains an invalid device ID")
            if not isinstance(name, str):
                logger.error("Hunonic device list contains an invalid device name")
                raise ValueError("Hunonic devices response contains an invalid device name")
            normalized.append({"id": str(device_id).strip(), "name": name.strip() or "Hunonic Device"})
        return normalized

    def send_command(self, device_id: str, cmd: str) -> bool:
        if not self.token:
            self.login()

        payload = json.dumps({
            "cmd": cmd.lower(),
            "data": 1
        })
        
        topic = f"hunonic/device/{device_id}/command"
        
        import paho.mqtt.client as mqtt
        client = mqtt.Client(client_id=f"ParkingAI_{uuid.uuid4().hex[:12]}")
        client.username_pw_set(self.phone, self.token)
        connected = False
        try:
            client.connect(self.MQTT_HOST, self.MQTT_PORT, 60)
            connected = True
        finally:
            if not connected:
                logger.error("Hunonic MQTT connection failed")
        client.loop_start()
        try:
            publish_completed = False
            try:
                result = client.publish(topic, payload, qos=1)
                result.wait_for_publish()
                publish_completed = True
            finally:
                if not publish_completed:
                    logger.error("Hunonic MQTT publish transport failed")
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error("Hunonic MQTT publish failed with code %s", result.rc)
                raise RuntimeError("Hunonic MQTT publish failed")
        finally:
            client.loop_stop()
            client.disconnect()
        logger.info("Sent Hunonic command '%s' to device %s", cmd, device_id)
        return True
