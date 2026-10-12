import requests
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
        if not resp.ok:
            logger.error("Imou API %s returned HTTP %s", endpoint, resp.status_code)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict) or not isinstance(body.get("result"), dict):
            logger.error("Imou API %s returned an invalid envelope", endpoint)
            raise ValueError("Imou API response has an invalid envelope")
        result = body["result"]
        raw_code = result.get("code")
        if isinstance(raw_code, bool) or not isinstance(raw_code, (str, int)):
            logger.error("Imou API %s returned an invalid status code", endpoint)
            raise ValueError("Imou API response has an invalid status code")
        code = str(raw_code)
        if code != "0":
            if code == "TK1002":
                self._token = None
            logger.error("Imou API %s failed with code %r", endpoint, code[:32])
            raise RuntimeError("Imou API request failed")
        data = result.get("data")
        if not isinstance(data, dict):
            logger.error("Imou API %s returned non-object data", endpoint)
            raise ValueError("Imou API response data must be an object")
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
        
        token = data.get("accessToken")
        if not isinstance(token, str) or not token.strip():
            logger.error("Imou access-token response has no valid token")
            raise ValueError("Imou API response is missing a valid access token")
        raw_expiry = data.get("expireTime", 3600)
        if isinstance(raw_expiry, bool) or not (
            isinstance(raw_expiry, int) or isinstance(raw_expiry, str) and raw_expiry.isdecimal()
        ):
            logger.error("Imou access-token response has an invalid expiry")
            raise ValueError("Imou API response has an invalid token expiry")
        expires_in = int(raw_expiry)
        if not 1 <= expires_in <= 365 * 24 * 3600:
            logger.error("Imou access-token response has an out-of-range expiry")
            raise ValueError("Imou API response has an invalid token expiry")
        self._token = token.strip()
        self._token_expires = now + expires_in
        return self._token

    def get_device_detail(self, device_id: str) -> dict:
        token = self.get_access_token()
        data = self._call("listDeviceDetailsByIds", {
            "token": token,
            "deviceList": [{"deviceId": device_id}]
        })
        dev_list = data.get("deviceList")
        if not isinstance(dev_list, list) or any(not isinstance(dev, dict) for dev in dev_list):
            logger.error("Imou listDeviceDetailsByIds returned an invalid device list")
            raise ValueError("Imou device detail list is invalid")
        detail = dev_list[0] if dev_list else {}
        for field in ("deviceName", "deviceModel", "lanIp", "deviceLocalIp", "deviceIp", "ipAddress"):
            if detail.get(field) is not None and not isinstance(detail[field], str):
                logger.error("Imou listDeviceDetailsByIds returned invalid field %s", field)
                raise ValueError("Imou device detail has an invalid field")
        for field in ("status", "deviceOnline", "online", "deviceStatus", "state"):
            if detail.get(field) is not None and not isinstance(detail[field], (str, int, bool)):
                logger.error("Imou listDeviceDetailsByIds returned invalid status field %s", field)
                raise ValueError("Imou device detail has an invalid status")
        return detail

    def get_device_network_info(self, device_id: str, channel_id: str = "0") -> dict | None:
        token = self.get_access_token()
        data = self._call("getDeviceCameraStatus", {
            "token": token,
            "deviceId": device_id,
            "enableType": "closeCamera",
            "channelId": channel_id
        })
        ip = data.get("localIp") or data.get("lanIp") or data.get("ip")
        if ip is not None and not isinstance(ip, str):
            logger.error("Imou getDeviceCameraStatus returned an invalid address")
            raise ValueError("Imou camera status has an invalid address")
        if ip and ip != "0.0.0.0":
            logger.info(f"  [CameraStatus] Found IP {ip} for {device_id}")
            return {"ip": ip, "source": "cameraStatus"}
        data = self._call("getDeviceNetInfo", {
            "token": token,
            "deviceId": device_id,
        })
        ip = data.get("lanIp") or data.get("localIp") or data.get("ip")
        if ip is not None and not isinstance(ip, str):
            logger.error("Imou getDeviceNetInfo returned an invalid address")
            raise ValueError("Imou network info has an invalid address")
        if ip and ip != "0.0.0.0":
            logger.info("  [NetInfo] Found IP %s for %s", ip, device_id)
            return {"ip": ip, "source": "netInfo"}
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
        base_list = data.get("deviceList")
        if not isinstance(base_list, list) or any(not isinstance(dev, dict) for dev in base_list):
            logger.error("Imou deviceBaseList returned an invalid device list")
            raise ValueError("Imou device list is invalid")
        
        final_list = []
        for dev in base_list:
            did_raw = dev.get('deviceId')
            if isinstance(did_raw, bool) or not isinstance(did_raw, (str, int)) or not str(did_raw).strip():
                logger.error("Imou deviceBaseList returned a device with an invalid ID")
                raise ValueError("Imou device has an invalid ID")
            for field in ("deviceName", "deviceModel", "lanIp", "deviceLocalIp", "ipAddress"):
                if dev.get(field) is not None and not isinstance(dev[field], str):
                    logger.error("Imou deviceBaseList returned invalid field %s", field)
                    raise ValueError("Imou device has an invalid field")
            for field in ("status", "deviceOnline", "online", "deviceStatus", "state"):
                if dev.get(field) is not None and not isinstance(dev[field], (str, int, bool)):
                    logger.error("Imou deviceBaseList returned invalid status field %s", field)
                    raise ValueError("Imou device has an invalid status")
            device_id = str(did_raw).strip()
            did = device_id.lower()
            dev['deviceId'] = device_id
            dev['deviceModel'] = dev.get('deviceModel') or 'Imou Camera'
            logger.info(f"Imou Discovery: Processing device {did}")
            
            fdev = self.get_device_detail(device_id)
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

            current_ip = dev.get('deviceLocalIp')
            if not current_ip or current_ip == "0.0.0.0":
                net_info = self.get_device_network_info(device_id)
                if net_info is not None:
                    dev['deviceLocalIp'] = net_info['ip']
                    logger.info(f"  [NET-FALLBACK] Found IP {net_info['ip']} for {did} via {net_info['source']}")

            final_list.append(dev)

        logger.info(f"Merged Imou device list: {len(final_list)} entries.")
        return final_list
