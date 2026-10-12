import time
import logging
import re
import sys
import tinytuya
from contextlib import closing
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlparse
from backend.iot.door_config import normalize_relay_config
from backend.iot.hunonic_handler import HunonicHandler
from backend.services.settings_service import get_app_setting

logger = logging.getLogger("door_controller")
_RELAY_ENDPOINT = re.compile(
    r"(?P<scheme>https?)://(?P<host>\[[0-9A-Fa-f:.%]+\]|[A-Za-z0-9.-]+)"
    r"(?::(?P<port>[0-9]{1,5}))?"
    r"(?P<target>[/?][\x21-\x22\x24-\x3F\x41-\x5B\x5D-\x7E]*)?\Z",
    re.IGNORECASE,
)

def _relay_url(url: str) -> str | None:
    normalized = url if "://" in url else f"http://{url}"
    p = urlparse(normalized)
    host = (p.hostname or "").lower()
    safe = p.scheme in ("http", "https") and bool(host) and not (
        host.startswith("169.254.") or host == "metadata" or host.endswith(".internal")
    )
    return normalized if safe else None


def _send_relay_request(url: str):
    endpoint = _RELAY_ENDPOINT.fullmatch(url)
    if endpoint is None:
        logger.error("Relay request URL is invalid")
        raise ValueError("Invalid relay URL")
    host = endpoint.group("host").strip("[]")
    scheme = endpoint.group("scheme").lower()
    port = int(endpoint.group("port") or (443 if scheme == "https" else 80))
    if not 1 <= port <= 65535:
        logger.error("Relay request port is invalid")
        raise ValueError("Invalid relay port")
    target = endpoint.group("target") or "/"
    if target.startswith("?"):
        target = "/" + target
    connection_type = HTTPSConnection if scheme == "https" else HTTPConnection
    try:
        with closing(connection_type(host, port, timeout=5)) as connection:
            connection.request("GET", target)
            status = connection.getresponse().status
    finally:
        error_type = sys.exc_info()[0]
        if error_type is not None:
            logger.error("Relay transport failed (%s)", error_type.__name__)
    if not 200 <= status < 300:
        logger.error("Relay request failed with HTTP %s", status)
        raise RuntimeError(f"Relay request failed with HTTP {status}")

class DoorController:
    def __init__(self):
        self.RELAY_TYPE = "tuya_local"
        
        self.RELAY_GPIO_PIN = 11
        
        self.RELAY_HTTP_URL = ""
        
    def control_door(self, action: str, relay_type: str = None, relay_config: dict = None) -> bool:
        if action not in ("OPEN", "CLOSE"):
            logger.error("Door action is invalid")
            raise ValueError("Door action must be OPEN or CLOSE")
        rtype = relay_type or self.RELAY_TYPE
        rconfig = relay_config if relay_config is not None else {}
        if not isinstance(rconfig, dict):
            logger.error("Relay configuration must be an object")
            raise TypeError("Relay configuration must be an object")

        if rtype == "tuya_local":
            rconfig = {
                **rconfig,
                "device_id": rconfig.get("device_id") or get_app_setting("TUYA_DEVICE_ID", ""),
                "local_key": rconfig.get("local_key") or get_app_setting("TUYA_LOCAL_KEY", ""),
                "ip": rconfig.get("ip") or get_app_setting("TUYA_IP", "") or "Auto",
            }
        elif rtype in ("tasmota", "http"):
            rconfig = {**rconfig, "ip": rconfig.get("ip") or self.RELAY_HTTP_URL}
        elif rtype == "gpio":
            rconfig = {**rconfig, "pin": rconfig.get("pin") or self.RELAY_GPIO_PIN}
        rconfig = normalize_relay_config(rtype, rconfig)

        logger.info("Controlling door: %s (Type: %s)", action, rtype)

        if rtype == "gpio":
            import OPi.GPIO as GPIO

            pin = rconfig["pin"]
            GPIO.setmode(GPIO.BOARD)
            GPIO.setup(pin, GPIO.OUT)
            try:
                GPIO.output(pin, GPIO.HIGH)
                time.sleep(0.5)
                GPIO.output(pin, GPIO.LOW)
            finally:
                GPIO.cleanup()
            return True

        if rtype == "tasmota":
            relay_url = _relay_url(rconfig["ip"])
            if relay_url is None:
                logger.error("Tasmota relay URL is invalid")
                raise ValueError("Invalid Tasmota relay URL")
            _send_relay_request(f"{relay_url.rstrip('/')}/cm?cmnd=Power%20TOGGLE")
            return True

        elif rtype == "http":
            relay_url = _relay_url(rconfig["ip"])
            if relay_url is None:
                logger.error("HTTP relay URL is invalid")
                raise ValueError("Invalid HTTP relay URL")
            _send_relay_request(relay_url)
            logger.info("Sent HTTP relay request")
            return True

        elif rtype == "tuya_local":
            device = tinytuya.OutletDevice(
                dev_id=rconfig["device_id"],
                address=rconfig["ip"],
                local_key=rconfig["local_key"],
            )
            device.set_version(3.3)
            status = device.status()
            dps = status.get("dps") if isinstance(status, dict) else None
            if not isinstance(dps, dict) or not isinstance(dps.get("1"), bool):
                logger.error("Tuya relay status is missing a valid switch state")
                raise RuntimeError("Tuya relay status is missing a valid switch state")
            current = dps["1"]
            result = device.set_value("1", not current)
            if isinstance(result, dict) and result.get("Error"):
                logger.error("Tuya relay command failed")
                raise RuntimeError("Tuya relay command failed")
            logger.info("tuya_local toggle OK — DPS '1': %s -> %s", current, not current)
            return True

        elif rtype == "hunonic_cloud":
            handler = HunonicHandler(rconfig["phone"], rconfig["password"])
            return handler.send_command(rconfig["device_id"], action.lower())

        logger.error("Unsupported relay type")
        raise ValueError("Unsupported relay type")
