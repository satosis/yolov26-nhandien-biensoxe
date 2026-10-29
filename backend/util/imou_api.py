"""Backward compatibility shim for Imou client API.
Implementation has moved to `backend.util.network.imou_api`.
"""
from backend.util.network.imou_api import ImouClient

__all__ = ["ImouClient"]
