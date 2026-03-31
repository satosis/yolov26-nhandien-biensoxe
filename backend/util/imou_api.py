import requests
import json
import logging
import time

logger = logging.getLogger("imou_api")

class ImouClient:
    """Simple client for Imou Open Platform API (Cloud)."""
    
    BASE_URL = "https://openapi.imoulife.com/openapi"
    
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expires = 0

    def get_access_token(self) -> str | None:
        """Fetch or return cached accessToken."""
        now = time.time()
        if self._token and now < self._token_expires - 60:
            return self._token
            
        logger.info("Fetching new Imou AccessToken...")
        url = f"{self.BASE_URL}/accessToken"
        payload = {
            "id": "1",
            "appId": self.app_id,
            "appSecret": self.app_secret,
            "v": "2.0"
        }
        
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("result", {}).get("code") == "0":
            res_data = data["result"]["data"]
            self._token = res_data["accessToken"]
            # expireTime is in seconds from now
            expires_in = int(res_data.get("expireTime", 3600))
            self._token_expires = now + expires_in
            return self._token
        else:
            logger.error(f"Failed to get Imou token: {data}")
            return None

    def list_devices(self, page_size: int = 50) -> list:
        """
        Fetch bound devices and their details.
        Returns a list of device dicts with 'deviceId', 'deviceLocalIp', 'deviceStatus', etc.
        """
        token = self.get_access_token()
        if not token:
            return []
            
        url = f"{self.BASE_URL}/listDeviceDetails"
        payload = {
            "id": "1",
            "token": token,
            "params": {
                "queryRange": f"1-{page_size}",
            },
            "v": "1.0"
        }
        
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if data.get("result", {}).get("code") == "0":
            return data["result"]["data"].get("deviceList", [])
        else:
            logger.error(f"Failed to list Imou devices: {data}")
            return []

    def get_device_info(self, device_id: str) -> dict | None:
        """Get details for a specific device."""
        devices = self.list_devices()
        for d in devices:
            if d.get("deviceId") == device_id:
                return d
        return None
