import requests
import json
import logging
import time
import uuid
import hashlib

logger = logging.getLogger("imou_api")

class ImouClient:
    """Client for Imou Open Platform API (International / easy4ip)."""
    
    BASE_URL = "https://openapi.easy4ip.com/openapi"
    
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expires = 0

    def _make_sign(self, ts: str, nonce: str) -> str:
        """Create MD5 signature as required by Imou Open Platform."""
        # Standard format for International: time:TIME,nonce:NONCE,appSecret:APP_SECRET
        sign_str = f"time:{ts},nonce:{nonce},appSecret:{self.app_secret}"
        return hashlib.md5(sign_str.encode()).hexdigest()

    def _call(self, endpoint: str, params: dict, timeout: int = 15) -> dict:
        """Helper to make authenticated POST requests to Imou API."""
        ts = str(int(time.time()))
        nonce = str(uuid.uuid4())
        sign = self._make_sign(ts, nonce)
        
        payload = {
            "system": {
                "ver": "1.1",
                "sign": sign,
                "appId": self.app_id,
                "time": int(ts),
                "nonce": nonce
            },
            "params": params,
            "id": f"req_{ts}"
        }
        
        url = f"{self.BASE_URL}/{endpoint}"
        logger.info(f"Calling Imou API: {endpoint}")
        
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            data = resp.json()
            
            # The structure is often { "result": { "code": "0", "data": { ... } } }
            result = data.get("result", {})
            code = str(result.get("code", "0"))
            
            if code != "0":
                err_msg = result.get("msg", "Unknown error")
                logger.error(f"Imou API error [{code}]: {err_msg}")
                # If token expired, clear cache
                if code == "TK1002":
                    self._token = None
                raise Exception(f"Imou API Error: [{code}] {err_msg}")
            
            data_out = result.get("data", {})
            logger.debug(f"Imou API Response [{endpoint}]: {json.dumps(data_out)[:500]}...")
            return data_out
        except Exception as e:
            logger.error(f"Network error calling Imou {endpoint}: {e}")
            raise e

    def get_access_token(self) -> str:
        """Fetch or return cached accessToken."""
        now = time.time()
        if self._token and now < self._token_expires - 60:
            return self._token
            
        logger.info("Fetching new Imou AccessToken...")
        data = self._call("accessToken", {
            "appId": self.app_id,
            "appSecret": self.app_secret
        })
        
        self._token = data["accessToken"]
        # expires_in is in seconds
        expires_in = int(data.get("expireTime", 3600))
        self._token_expires = now + expires_in
        return self._token

    def get_device_detail(self, device_id: str) -> dict:
        """Fetch full details for a single device, including local IP."""
        token = self.get_access_token()
        data = self._call("listDeviceDetailsByIds", {
            "token": token,
            "deviceList": [{"deviceId": device_id}]
        })
        # Returns a list of devices usually
        dev_list = data.get("deviceList", [])
        return dev_list[0] if dev_list else {}

    def list_devices(self, limit: int = 50) -> list:
        """
        Fetch devices associated with the account.
        Uses deviceBaseList and merges with detail-level info to ensure local IP.
        """
        token = self.get_access_token()
        
        # 1. Base list (fast)
        data = self._call("deviceBaseList", {
            "token": token,
            "bindId": -1,
            "limit": limit,
            "type": "bindAndShare",
            "needApInfo": True
        })
        base_list = data.get("deviceList", [])
        
        # 2. Parallel deep-dive for each online device to find the IP
        final_list = []
        for dev in base_list:
            did_raw = dev.get('deviceId', '')
            did = str(did_raw).strip().lower()
            logger.info(f"Imou Discovery: Processing device {did}")
            
            # Try listDeviceDetailsByIds for this specific device
            try:
                fdev = self.get_device_detail(did_raw)
                if fdev:
                    # Update with detail info (especially localIp)
                    possible_ips = [
                        fdev.get('lanIp'), fdev.get('deviceLocalIp'), fdev.get('deviceIp'), 
                        dev.get('lanIp'), dev.get('deviceLocalIp'), dev.get('ipAddress')
                    ]
                    resolved_ip = next((ip for ip in possible_ips if ip and ip != "0.0.0.0"), None)
                    if resolved_ip:
                        dev['deviceLocalIp'] = resolved_ip
                        logger.info(f"  [DEEP-DIVE] Found IP {resolved_ip} for {did}")
                    
                    # Merge other fields
                    dev.update({k: v for k, v in fdev.items() if v and not dev.get(k)})
            except Exception as e:
                logger.warning(f"  [DEEP-DIVE] Failed detailed info for {did}: {e}")

            final_list.append(dev)
            
        # --- Diagnostic Dump ---
        try:
            debug_path = "/tmp/imou_debug.json"
            with open(debug_path, "w") as f:
                json.dump({"merged": final_list}, f, indent=2)
            logger.info(f"Imou Discovery: Debug data dumped to {debug_path}")
        except:
            pass

        logger.info(f"Merged Imou device list: {len(final_list)} entries.")
        return final_list
