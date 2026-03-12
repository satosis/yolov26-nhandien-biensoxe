from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
import asyncio
import json
import os
from typing import List

from telemetry import get_system_metrics
from bot_controller import BotController

app = FastAPI(title="Telegram Bot Management Dashboard")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
BOT_SCRIPT = os.path.join(PROJECT_ROOT, "services", "bot_standalone.py")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

from auth import (
    verify_password, get_password_hash, create_access_token, 
    get_current_user, oauth2_scheme
)
from core.database import DatabaseManager
from core.config import DB_PATH

app = FastAPI(title="Telegram Bot Management Dashboard")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
BOT_SCRIPT = os.path.join(PROJECT_ROOT, "services", "bot_standalone.py")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# Components
db = DatabaseManager()
bot_manager = BotController(BOT_SCRIPT)

# Initial Setup
def init_admin():
    if not db.get_user("admin"):
        hashed = get_password_hash("admin123")
        db.create_user("admin", hashed, "admin")
        print("👤 Created default admin: admin / admin123")

init_admin()

class Token(BaseModel):
    access_token: str
    token_type: str

@app.post("/api/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = db.get_user(form_data.username)
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(
        data={"sub": user["username"], "scopes": [user["role"]]}
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/me")
async def read_users_me(current_user: dict = Security(get_current_user)):
    return current_user

# Bot Control (Protected)
@app.post("/api/bot/start")
async def start_bot(current_user: dict = Security(get_current_user, scopes=["admin", "manager"])):
    success, msg = bot_manager.start()
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}

@app.post("/api/bot/stop")
async def stop_bot(current_user: dict = Security(get_current_user, scopes=["admin"])):
    success, msg = bot_manager.stop()
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}

@app.post("/api/bot/restart")
async def restart_bot(current_user: dict = Security(get_current_user, scopes=["admin"])):
    success, msg = bot_manager.restart()
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}

@app.get("/api/bot/status")
async def get_status(current_user: dict = Security(get_current_user)):
    return bot_manager.get_status()

@app.post("/api/bot/heartbeat")
async def heartbeat(current_user: dict = Security(get_current_user, scopes=["admin", "manager"])):
    from services.telegram_service import notify_telegram
    try:
        notify_telegram("💓 Heartbeat test from Dashboard", important=True)
        return {"message": "Heartbeat sent to Admin Chat ID"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot/config")
async def update_config(config: dict, current_user: dict = Security(get_current_user, scopes=["admin"])):
    # In a real app, we would update .env or a config file
    # For now, let's just log it
    print(f"Update Config: {config}")
    # Example: update core.config or similar
    return {"message": "Configuration updated (Mock)"}

# WebSockets
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            metrics = get_system_metrics()
            # Add bot stats if available
            stats_path = os.path.join(PROJECT_ROOT, "config", "bot_stats.json")
            if os.path.exists(stats_path):
                with open(stats_path, "r") as f:
                    metrics["bot_stats"] = json.load(f)
            
            await websocket.send_json(metrics)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    last_idx = 0
    try:
        while True:
            logs = bot_manager.get_logs()
            if len(logs) > last_idx:
                new_logs = logs[last_idx:]
                await websocket.send_json({"logs": new_logs})
                last_idx = len(logs)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass

# UI Files
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8082)
