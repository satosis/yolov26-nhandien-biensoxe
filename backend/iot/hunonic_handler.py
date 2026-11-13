import requests
import json
import logging
import os
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
        
    def login(self) -> bool:
        url = f"{self.API_BASE}/auth/login"
        payload = {
            "phone": self.phone,
            "password": self.password
        }
        
        response = requests.post(url, json=payload, timeout=10)
        if not response.ok:
            logger.error("Hunonic login failed with HTTP %s", response.status_code)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Hunonic login response must be an object")
        token = str(data.get("access_token") or data.get("token") or "").strip()
        if not token:
            raise ValueError("Hunonic login response is missing a valid access token")
        user_id = data.get("user_id") if data.get("user_id") is not None else data.get("id")
        self.token = token
        self.user_id = str(user_id) if user_id is not None else None
        logger.info("Hunonic login successful")
        return True

    def get_devices(self) -> List[Dict]:
        if not self.token:
            self.login()
        url = f"{self.API_BASE}/devices"
        headers = {"Authorization": f"Bearer {self.token}"}
        response = requests.get(url, headers=headers, timeout=10)
        if not response.ok:
            logger.error("Hunonic device list failed with HTTP %s", response.status_code)
        response.raise_for_status()
        data = response.json()
        devices = data.get("devices") if isinstance(data, dict) else None
        if not isinstance(devices, list):
            raise ValueError("Hunonic devices response must contain a list")
        normalized = []
        for device in devices:
            if not isinstance(device, dict):
                continue
            device_id = device.get("id") or device.get("device_id") or device.get("deviceId")
            if not device_id or isinstance(device_id, bool):
                continue
            name = str(device.get("name") or device.get("deviceName") or "Hunonic Device").strip()
            normalized.append({"id": str(device_id).strip(), "name": name or "Hunonic Device"})
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
        client.connect(self.MQTT_HOST, self.MQTT_PORT, 60)
        client.loop_start()
        try:
            result = client.publish(topic, payload, qos=1)
            result.wait_for_publish()
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error("Hunonic MQTT publish failed with code %s", result.rc)
                raise RuntimeError("Hunonic MQTT publish failed")
        finally:
            client.loop_stop()
            client.disconnect()
        logger.info("Sent Hunonic command '%s' to device %s", cmd, device_id)
        return True
