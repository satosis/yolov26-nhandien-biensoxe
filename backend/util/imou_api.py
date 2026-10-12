import requests
import json
import logging
import time
import uuid
import hashlib

logger = logging.getLogger("imou_api")

class ImouClient:
    
    BASE_URL = "https://openapi.easy4ip.com/openapi"
    
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expires = 0

    def _make_sign(self, ts: str, nonce: str) -> str:
        sign_str = f"time:{ts},nonce:{nonce},appSecret:{self.app_secret}"
        return hashlib.md5(sign_str.encode()).hexdigest()

    def _call(self, endpoint: str, params: dict, timeout: int = 15) -> dict:
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
        
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        result = resp.json()["result"]
        code = str(result["code"])
        if code != "0":
            if code == "TK1002":
                self._token = None
            raise RuntimeError(f"Imou API {endpoint} error [{code}]: {result.get('msg', 'Unknown error')}")
        data = result["data"]
        logger.debug("Imou API Response [%s]: %s...", endpoint, json.dumps(data)[:500])
        return data

    def get_access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expires - 60:
            return self._token
            
        logger.info("Fetching new Imou AccessToken...")
        data = self._call("accessToken", {
            "appId": self.app_id,
            "appSecret": self.app_secret
        })
        
        self._token = data["accessToken"]
        expires_in = int(data.get("expireTime", 3600))
        self._token_expires = now + expires_in
        return self._token

    def get_device_detail(self, device_id: str) -> dict:
        token = self.get_access_token()
        data = self._call("listDeviceDetailsByIds", {
            "token": token,
            "deviceList": [{"deviceId": device_id}]
        })
        dev_list = data.get("deviceList", [])
        return dev_list[0] if dev_list else {}

    def get_device_network_info(self, device_id: str, channel_id: str = "0") -> dict | None:
        token = self.get_access_token()

        try:
            data = self._call("getDeviceCameraStatus", {
                "token": token,
                "deviceId": device_id,
                "enableType": "closeCamera",
                "channelId": channel_id
            })
            ip = data.get("localIp") or data.get("lanIp") or data.get("ip")
            if ip and ip != "0.0.0.0":
                logger.info(f"  [CameraStatus] Found IP {ip} for {device_id}")
                return {"ip": ip, "source": "cameraStatus"}
        except Exception as e:
            logger.debug(f"  getDeviceCameraStatus fallback failed for {device_id}: {e}")

        try:
            data = self._call("getDeviceNetInfo", {
                "token": token,
                "deviceId": device_id
            })
            ip = data.get("lanIp") or data.get("localIp") or data.get("ip")
            if ip and ip != "0.0.0.0":
                logger.info(f"  [NetInfo] Found IP {ip} for {device_id}")
                return {"ip": ip, "source": "netInfo"}
        except Exception as e:
            logger.debug(f"  getDeviceNetInfo fallback failed for {device_id}: {e}")

        return None

    def list_devices(self, limit: int = 50) -> list:
        token = self.get_access_token()
        
        data = self._call("deviceBaseList", {
            "token": token,
            "bindId": -1,
            "limit": limit,
            "type": "bindAndShare",
            "needApInfo": True
        })
        base_list = data.get("deviceList", [])
        
        final_list = []
        for dev in base_list:
            did_raw = dev.get('deviceId', '')
            did = str(did_raw).strip().lower()
            logger.info(f"Imou Discovery: Processing device {did}")
            
            try:
                fdev = self.get_device_detail(did_raw)
                if fdev:
                    possible_ips = [
                        fdev.get('lanIp'), fdev.get('deviceLocalIp'), fdev.get('deviceIp'),
                        dev.get('lanIp'), dev.get('deviceLocalIp'), dev.get('ipAddress')
                    ]
                    resolved_ip = next((ip for ip in possible_ips if ip and ip != "0.0.0.0"), None)
                    if resolved_ip:
                        dev['deviceLocalIp'] = resolved_ip
                        logger.info(f"  [DEEP-DIVE] Found IP {resolved_ip} for {did}")

                    dev.update({k: v for k, v in fdev.items() if v and not dev.get(k)})
            except Exception as e:
                logger.warning(f"  [DEEP-DIVE] Failed detailed info for {did}: {e}")

            current_ip = dev.get('deviceLocalIp')
            if not current_ip or current_ip == "0.0.0.0":
                try:
                    net_info = self.get_device_network_info(did_raw)
                    if net_info and net_info.get('ip'):
                        dev['deviceLocalIp'] = net_info['ip']
                        logger.info(f"  [NET-FALLBACK] Found IP {net_info['ip']} for {did} via {net_info['source']}")
                except Exception as e:
                    logger.debug(f"  [NET-FALLBACK] Failed for {did}: {e}")

            final_list.append(dev)

        logger.info(f"Merged Imou device list: {len(final_list)} entries.")
        return final_list
