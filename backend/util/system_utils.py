import os
import shutil
import logging
import platform

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

logger = logging.getLogger(__name__)

def get_system_stats():
    """
    Returns a dictionary of system resource usage.
    """
    stats = {
        "cpu_percent": 0.0,
        "memory_percent": 0.0,
        "disk_percent": 0.0,
        "load_avg": [0.0, 0.0, 0.0]
    }

    if _HAS_PSUTIL:
        try:
            stats["cpu_percent"] = psutil.cpu_percent(interval=None)
            stats["memory_percent"] = psutil.virtual_memory().percent
            usage = psutil.disk_usage('/')
            stats["disk_percent"] = usage.percent
            if hasattr(os, 'getloadavg'):
                stats["load_avg"] = list(os.getloadavg())
        except Exception as e:
            logger.debug(f"Error getting psutil stats: {e}")
    else:
        # Fallback for basic disk usage
        try:
            total, used, free = shutil.disk_usage("/")
            stats["disk_percent"] = (used / total) * 100
            if hasattr(os, 'getloadavg'):
                stats["load_avg"] = list(os.getloadavg())
        except Exception:
            pass
            
    return stats

def log_system_health():
    """
    Logs current system health status.
    """
    stats = get_system_stats()
    logger.info(
        f"System Health: CPU {stats['cpu_percent']}% | "
        f"MEM {stats['memory_percent']}% | "
        f"DISK {stats['disk_percent']}% | "
        f"LOAD {stats['load_avg']}"
    )
    
    # Alert if disk is critically low
    if stats['disk_percent'] > 90:
        logger.warning(f"CRITICAL: Disk usage is over 90% ({stats['disk_percent']:.1f}%)")
