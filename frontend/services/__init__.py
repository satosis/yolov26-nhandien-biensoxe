"""Services Module: Quản lý từng dịch vụ độc lập cho chiến dịch quảng cáo và landing page."""

from .crane.router import router as crane_router
from .warehouse.router import router as warehouse_router
from .scale.router import router as scale_router

__all__ = ["crane_router", "warehouse_router", "scale_router"]
