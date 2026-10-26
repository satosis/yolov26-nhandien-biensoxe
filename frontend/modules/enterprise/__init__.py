"""Enterprise management module."""
from frontend.modules.enterprise.router import router as enterprise_router
from frontend.modules.enterprise.supplier_router import router as supplier_router

__all__ = ["enterprise_router", "supplier_router"]
