import time
import logging
import tinytuya
from contextlib import closing
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlsplit
from backend.iot.hunonic_handler import HunonicHandler

logger = logging.getLogger("door_controller")

def _send_relay_request(url: str):
    endpoint = urlsplit(url)
    target = endpoint.path or "/"
    if endpoint.query:
        target += "?" + endpoint.query
    connection_type = HTTPSConnection if endpoint.scheme == "https" else HTTPConnection
    with closing(connection_type(endpoint.hostname, endpoint.port, timeout=5)) as connection:
        connection.request("GET", target)
        status = connection.getresponse().status
    if not 200 <= status < 300:
        logger.error("Relay request failed with HTTP %s", status)
        raise RuntimeError(f"Relay request failed with HTTP {status}")

class DoorController:
    def control_door(self, action: str, relay_type: str, relay_config: dict) -> bool:
        """Execute a command with config normalized by the form or DB reader."""
        logger.info("Controlling door: %s (Type: %s)", action, relay_type)

        if relay_type == "gpio":
            import OPi.GPIO as GPIO

            pin = relay_config["pin"]
            GPIO.setmode(GPIO.BOARD)
            GPIO.setup(pin, GPIO.OUT)
            try:
                GPIO.output(pin, GPIO.HIGH)
                time.sleep(0.5)
                GPIO.output(pin, GPIO.LOW)
            finally:
                GPIO.cleanup()
            return True

        if relay_type == "tasmota":
            relay_url = relay_config["ip"]
            _send_relay_request(f"{relay_url.rstrip('/')}/cm?cmnd=Power%20TOGGLE")
            return True

        elif relay_type == "http":
            _send_relay_request(relay_config["ip"])
            logger.info("Sent HTTP relay request")
            return True

        elif relay_type == "tuya_local":
            device = tinytuya.OutletDevice(
                dev_id=relay_config["device_id"],
                address=relay_config["ip"],
                local_key=relay_config["local_key"],
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

        elif relay_type == "hunonic_cloud":
            handler = HunonicHandler(relay_config["phone"], relay_config["password"])
            return handler.send_command(relay_config["device_id"], action.lower())

        logger.error("Unsupported relay type")
        raise ValueError("Unsupported relay type")
