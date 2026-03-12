import psutil
import os
import time

def get_system_metrics():
    """Fetch real-time CPU, RAM, and Temperature metrics."""
    cpu_usage = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory()
    memory_usage = memory.percent
    
    # Temperature (Common for Orange Pi / Linux)
    temp = None
    try:
        # Check standard thermal zone (Linux)
        temp_files = [
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/thermal/thermal_zone1/temp"
        ]
        for tf in temp_files:
            if os.path.exists(tf):
                with open(tf, 'r') as f:
                    # Value is in millidegrees Celsius
                    temp = int(f.read().strip()) / 1000.0
                    break
    except:
        pass
    
    # Fallback to psutil sensors if available
    if temp is None:
        try:
            sensors = psutil.sensors_temperatures()
            if 'cpu_thermal' in sensors:
                temp = sensors['cpu_thermal'][0].current
            elif 'soc_thermal' in sensors:
                temp = sensors['soc_thermal'][0].current
        except:
            temp = 0.0

    return {
        "cpu": cpu_usage,
        "ram": memory_usage,
        "temp": temp or 0.0,
        "timestamp": time.time()
    }
