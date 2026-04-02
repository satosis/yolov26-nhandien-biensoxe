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

    def list_devices(self, limit: int = 50) -> list:
        """
        Fetch devices associated with the account.
        Uses deviceBaseList to catch devices added via Imou Life App.
        """
        token = self.get_access_token()
        
        # deviceBaseList is more comprehensive than deviceList for mobile-added devices
        data = self._call("deviceBaseList", {
            "token": token,
            "bindId": -1,
            "limit": limit,
            "type": "bindAndShare",
            "needApInfo": True # Try getting more info
        })
        
        # Merge with deviceList to ensure we get IPs
        full_data = self._call("deviceList", {
            "token": token,
            "queryRange": f"1-{limit}"
        })
        
        base_list = data.get("deviceList", [])
        full_list = full_data.get("deviceList", [])
        
        # Create a case-insensitive map for easy lookup
        full_map = {str(d.get('deviceId', '')).strip().lower(): d for d in full_list}
        
        # Merge: prefer base_list structure but complement with full_data (like IP)
        final_list = []
        for dev in base_list:
            did_raw = dev.get('deviceId', '')
            did = str(did_raw).strip().lower()
            logger.info(f"Imou Discovery: Processing device {did}")
            
            if did in full_map:
                fdev = full_map[did]
                # Imou often uses 'lanIp' instead of 'deviceLocalIp' in some API versions
                # Some versions also use 'channelList' or 'channels' to report status
                possible_ips = [
                    fdev.get('lanIp'), fdev.get('deviceLocalIp'), fdev.get('deviceIp'), 
                    dev.get('lanIp'), dev.get('deviceLocalIp'), dev.get('ipAddress')
                ]
                possible_status = [
                    fdev.get('status'), fdev.get('deviceOnline'), fdev.get('online'), 
                    fdev.get('deviceStatus'), fdev.get('state'),
                    dev.get('status'), dev.get('deviceOnline'), dev.get('deviceStatus')
                ]
                
                resolved_ip = next((ip for ip in possible_ips if ip and ip != "0.0.0.0"), None)
                # Resolved status: prefer numeric (1/0) or explicit 'online'/'offline' strings
                resolved_status = next((st for st in possible_status if st is not None), None)
                
                if resolved_ip:
                    dev['deviceLocalIp'] = resolved_ip
                if resolved_status is not None:
                    dev['status'] = resolved_status
                
                # Merge the rest from full_map entry
                dev.update({k: v for k, v in fdev.items() if v and not dev.get(k)})
            
            # Final check for IP in the device object itself (it might be in 'lanIp' or others)
            if not dev.get('deviceLocalIp') or dev.get('deviceLocalIp') == "0.0.0.0":
                alt_ip = dev.get('lanIp') or dev.get('deviceIp') or dev.get('ipAddress')
                if alt_ip and alt_ip != "0.0.0.0":
                    dev['deviceLocalIp'] = alt_ip

            final_list.append(dev)
            
        # --- Diagnostic Dump ---
        try:
            debug_path = "/tmp/imou_debug.json"
            with open(debug_path, "w") as f:
                json.dump({"base": base_list, "full": full_list, "merged": final_list}, f, indent=2)
            logger.info(f"Imou Discovery: Debug data dumped to {debug_path}")
        except:
            pass

        logger.info(f"Merged Imou device list: {len(final_list)} entries.")
        return final_list
