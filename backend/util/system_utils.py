import os
import logging
import psutil

logger = logging.getLogger(__name__)

_DISK_ROOT = "C:\\" if os.name == "nt" else "/"

def get_system_stats():
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage(_DISK_ROOT).percent,
        "load_avg": list(os.getloadavg()),
    }

def log_system_health():
    stats = get_system_stats()
    logger.info(
        f"System Health: CPU {stats['cpu_percent']}% | "
        f"MEM {stats['memory_percent']}% | "
        f"DISK {stats['disk_percent']}% | "
        f"LOAD {stats['load_avg']}"
    )
    
    if stats['disk_percent'] > 90:
        logger.warning(f"CRITICAL: Disk usage is over 90% ({stats['disk_percent']:.1f}%)")
