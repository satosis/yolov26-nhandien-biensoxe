"""IoT & Automation Domain Module — Garage door controller, auto-close logic, Hunonic integration."""
from backend.iot.hunonic_handler import HunonicHandler
from backend.iot.door_controller import DoorController
from backend.iot.door_auto_close import DoorAutoCloseManager, CentroidTracker

__all__ = [
    "HunonicHandler",
    "DoorController",
    "DoorAutoCloseManager",
    "CentroidTracker",
]
