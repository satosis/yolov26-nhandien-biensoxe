"""Validate persisted relay settings before they reach a door controller."""

from collections.abc import Mapping
import logging
import re

logger = logging.getLogger(__name__)


_RELAY_TYPES = {"gpio", "tasmota", "http", "tuya_local", "hunonic_cloud"}
_HTTP_ADDRESS = re.compile(
    r"(?:(?:https?)://)?(?P<host>[A-Za-z0-9.-]+)(?::(?P<port>[0-9]{1,5}))?"
    r"(?:/[^\s#@\\]*)?(?:\?[^\s#@\\]*)?\Z",
    re.IGNORECASE,
)


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _http_address_error(value: object) -> str | None:
    if not _has_text(value):
        return "HTTP relay requires an address"
    address = value.strip()
    match = _HTTP_ADDRESS.fullmatch(address) if len(address) <= 2048 else None
    if match is None:
        return "HTTP relay address is invalid"
    host = match.group("host").lower()
    labels = host.split(".")
    if any(not label or label.startswith("-") or label.endswith("-") for label in labels):
        return "HTTP relay address is invalid"
    if host == "metadata" or host.endswith(".internal") or host.startswith("169.254."):
        return "HTTP relay address is not allowed"
    if len(labels) == 4 and all(label.isdigit() for label in labels):
        if any(int(label) > 255 for label in labels):
            return "HTTP relay address is invalid"
    port = match.group("port")
    if port is not None and not 0 < int(port) <= 65535:
        return "HTTP relay port is invalid"
    return None


def _relay_config_error(relay_type: str, relay_config: object) -> str | None:
    """Return a safe, field-only error message for a form or stored config."""
    if not isinstance(relay_type, str) or relay_type not in _RELAY_TYPES:
        return "Unsupported relay type"
    if not isinstance(relay_config, Mapping):
        return "Relay configuration must be an object"

    if relay_type == "hunonic_cloud":
        if not all(_has_text(relay_config.get(key)) for key in ("phone", "password", "device_id")):
            return "Hunonic requires phone, password, and device_id"
    elif relay_type == "tuya_local":
        if not all(_has_text(relay_config.get(key)) for key in ("device_id", "local_key")):
            return "Tuya requires device_id and local_key"
        ip = relay_config.get("ip")
        if ip is not None and not _has_text(ip):
            return "Tuya IP must be a non-empty string"
    elif relay_type in ("http", "tasmota"):
        address_error = _http_address_error(relay_config.get("ip"))
        if address_error:
            return address_error
    elif relay_type == "gpio":
        pin = relay_config.get("pin", 11)
        if isinstance(pin, bool) or not isinstance(pin, int) or pin <= 0:
            return "GPIO pin must be a positive integer"

    port = relay_config.get("port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 0 < port <= 65535):
        return "Relay port must be between 1 and 65535"
    return None


def relay_config_error(relay_type: str, relay_config: object) -> str | None:
    error = _relay_config_error(relay_type, relay_config)
    if error:
        logger.error("Invalid relay configuration: %s", error)
    return error


def with_tuya_defaults(relay_type: str, relay_config: object, defaults: Mapping) -> object:
    """Resolve legacy global Tuya settings at the data boundary."""
    if relay_type != "tuya_local" or not isinstance(relay_config, Mapping):
        return relay_config
    resolved = dict(relay_config)
    for key in ("device_id", "local_key", "ip"):
        if not resolved.get(key) and defaults.get(key):
            resolved[key] = defaults[key]
    return resolved


def normalize_relay_config(relay_type: str, relay_config: object) -> dict:
    """Return only fields used by this relay, or fail without exposing credentials."""
    error = relay_config_error(relay_type, relay_config)
    if error:
        raise ValueError(error)

    return canonical_relay_config(relay_type, relay_config)


def canonical_relay_config(relay_type: str, relay_config: Mapping) -> dict:
    """Canonicalize a relay config already checked by relay_config_error."""

    config = relay_config
    if relay_type == "hunonic_cloud":
        return {
            "phone": config["phone"].strip(),
            "password": config["password"],
            "device_id": config["device_id"].strip(),
        }
    if relay_type == "tuya_local":
        return {
            "device_id": config["device_id"].strip(),
            "local_key": config["local_key"],
            "ip": (config.get("ip") or "Auto").strip(),
        }
    if relay_type == "gpio":
        return {"pin": config.get("pin", 11)}

    address = config["ip"].strip()
    normalized = {"ip": address if "://" in address else f"http://{address}"}
    if config.get("port") is not None:
        normalized["port"] = config["port"]
    return normalized
