import os
import sys
import time
import json
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import DB_PATH
from core.database import DatabaseManager
from services.telegram_service import telegram_polling_loop, telegram_bot_handler
from core.door_controller import DoorController
from core.mqtt_manager import MQTTManager
import threading

# Stats tracking
STATS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "bot_stats.json")

stats = {
    "start_time": datetime.now().isoformat(),
    "messages_received": 0,
    "messages_sent": 0,
    "active_users": [],
    "errors": 0
}

def save_stats():
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f)
    except:
        pass

# Monkeypatch requests to track sent/received messages
import requests
original_post = requests.post
original_get = requests.get

def tracked_post(*args, **kwargs):
    if "api.telegram.org" in args[0] and "sendMessage" in args[0]:
        stats["messages_sent"] += 1
        save_stats()
    return original_post(*args, **kwargs)

def tracked_get(*args, **kwargs):
    resp = original_get(*args, **kwargs)
    if "api.telegram.org" in args[0] and "getUpdates" in args[0]:
        try:
            data = resp.json()
            if data.get("ok"):
                updates = data.get("result", [])
                if updates:
                    stats["messages_received"] += len(updates)
                    for u in updates:
                        if "message" in u:
                            uid = str(u["message"]["from"]["id"])
                            if uid not in stats["active_users"]:
                                stats["active_users"].append(uid)
                    save_stats()
        except:
            pass
    return resp

requests.post = tracked_post
requests.get = tracked_get

print("📊 Stats tracking initialized.")

# Mock functions for standalone run
def get_cpu_temp(): return 0.0
def get_state(): return (0, 0, False)

if __name__ == "__main__":
    print("🚀 Starting Standalone Telegram Bot...")
    db = DatabaseManager()
    door_controller = DoorController()
    mqtt_manager = MQTTManager(door_controller)
    mqtt_manager.start()
    
    # We would ideally monkeypatch or inject tracking into telegram_service.py
    # For now, let's just run them
    
    t1 = threading.Thread(target=telegram_polling_loop, args=(db, lambda: None, mqtt_manager), daemon=True)
    t2 = threading.Thread(target=telegram_bot_handler, args=(db, get_cpu_temp, get_state), daemon=True)
    
    t1.start()
    t2.start()
    
    print("✅ Bot threads started.")
    save_stats()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping bot...")
