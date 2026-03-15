import os
import json
import logging
import time
import paho.mqtt.client as mqtt
from core.database import DatabaseManager

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] EventBridge: %(message)s")
logger = logging.getLogger("event_bridge")

# --- Config ---
MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_TOPIC = "frigate/events"

# --- DB ---
db = DatabaseManager()

def on_connect(client, userdata, flags, rc):
    logger.info(f"Connected to MQTT with result code {rc}")
    client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        event_type = payload.get("type") # new, update, end
        after_data = payload.get("after", {})
        
        event_id = after_data.get("id")
        camera_id = after_data.get("camera")
        label = after_data.get("label")
        entered_zones = after_data.get("entered_zones", [])
        
        if event_type == "end" and label in ["car", "truck", "motorcycle"]:
            logger.info(f"Vehicle event ended: {event_id} ({label})")
            # Usually we would record this in DB if Frigate provides snapshots
            # For now, just logging.
            pass
            
    except Exception as e:
        logger.error(f"Error processing message: {e}")

def main():
    logger.info("Starting Event Bridge...")
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    retry_count = 0
    while retry_count < 10:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except Exception as e:
            logger.warning(f"MQTT Connect failed, retrying... ({e})")
            time.sleep(5)
            retry_count += 1

    client.loop_forever()

if __name__ == "__main__":
    main()
