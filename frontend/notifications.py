import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Annotated

logger = logging.getLogger(__name__)


class InquiryNotification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=10, pattern=r"^0(?:3|5|7|8|9)\d{8}$")]
    demand: str = Field(default="", max_length=2000)
    source_page: str = Field(default="home", max_length=80)

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        connections = self.active_connections[:]
        results = await asyncio.gather(
            *(connection.send_json(message) for connection in connections),
            return_exceptions=True,
        )
        errors = []
        for connection, result in zip(connections, results):
            if isinstance(result, WebSocketDisconnect):
                logger.info("Inquiry WebSocket disconnected during send")
                self.disconnect(connection)
            elif isinstance(result, asyncio.CancelledError):
                raise result
            elif isinstance(result, Exception):
                errors.append(result)
            elif isinstance(result, BaseException):
                raise result
        if errors:
            logger.error(
                "Inquiry WebSocket send failed for %s connection(s); first error type: %s",
                len(errors), type(errors[0]).__name__,
            )
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise RuntimeError(f"Inquiry WebSocket send failed for {len(errors)} connections") from errors[0]

manager = ConnectionManager()
