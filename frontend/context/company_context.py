"""Context variable management for active company across request lifecycle."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Optional

_current_company_id: ContextVar[Optional[int]] = ContextVar("current_company_id", default=None)


def set_current_company_id(company_id: Optional[int]) -> Token:
    """Set the active company ID for the current async task context."""
    val = int(company_id) if company_id and str(company_id).isdigit() and int(company_id) > 0 else None
    return _current_company_id.set(val)


def reset_current_company_id(token: Token) -> None:
    """Reset the company ID context back to previous token state."""
    try:
        _current_company_id.reset(token)
    except Exception:
        pass


def get_current_company_id(fallback: Optional[int] = None) -> Optional[int]:
    """Retrieve the current active company ID directly from context.
    
    If not explicitly set in context (e.g. CLI/tests), returns fallback.
    """
    cid = _current_company_id.get()
    if cid is not None and cid > 0:
        return cid
    return fallback


@contextmanager
def company_context(company_id: Optional[int]):
    """Context manager to temporarily set active company ID and automatically restore previous state."""
    token = set_current_company_id(company_id)
    try:
        yield
    finally:
        reset_current_company_id(token)
