"""In-memory presence and concurrent editing conflict detection service.

Tracks active editing sessions across users within a company (e.g. creating or editing invoices,
partner trades, customer debts) and detects conflicts when multiple users attempt to enter or
modify data for the same resource or the same customer.
"""

from __future__ import annotations

import logging
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# Session TTL in seconds. Heartbeats are sent every 3 seconds while the modal is open.
# If no heartbeat is received within 12 seconds, the session is expired.
SESSION_TTL_SECONDS = 12.0

# In-memory storage for active editing sessions:
# key: session_id -> dict with session metadata
_sessions: Dict[str, Dict[str, Any]] = {}


def _normalize_text(val: Optional[str]) -> str:
    """Normalize text for comparison (lowercase, strip diacritics and extra whitespace)."""
    if not val:
        return ""
    text = unicodedata.normalize("NFD", str(val).strip().lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.split())


def _purge_expired_locked(now: float) -> None:
    """Remove expired sessions. Must be called while holding _lock."""
    expired_keys = [
        sid for sid, data in _sessions.items()
        if now - data.get("last_heartbeat", 0) > SESSION_TTL_SECONDS
    ]
    for sid in expired_keys:
        _sessions.pop(sid, None)


def record_heartbeat(
    *,
    session_id: str,
    company_id: int,
    scope: str = "sales",
    user_id: int,
    user_name: str,
    resource_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    customer_name: Optional[str] = None,
    invoice_number: Optional[str] = None,
) -> Dict[str, Any]:
    """Record or update an editing heartbeat and check for conflicts with other users."""
    now = time.time()
    res_id = str(resource_id).strip() if resource_id is not None and str(resource_id).strip() not in ("", "None", "0") else ""
    cust_id = str(customer_id).strip() if customer_id is not None and str(customer_id).strip() not in ("", "None", "0") else ""
    cust_name_clean = str(customer_name or "").strip()
    norm_cust_name = _normalize_text(cust_name_clean)
    inv_num_clean = str(invoice_number or "").strip()
    norm_inv_num = _normalize_text(inv_num_clean)

    with _lock:
        _purge_expired_locked(now)

        existing = _sessions.get(session_id)
        created_at = existing["created_at"] if existing else now
        _sessions[session_id] = {
            "session_id": session_id,
            "company_id": int(company_id),
            "scope": scope,
            "user_id": int(user_id),
            "user_name": user_name or f"User #{user_id}",
            "resource_id": res_id,
            "customer_id": cust_id,
            "customer_name": cust_name_clean,
            "norm_customer_name": norm_cust_name,
            "invoice_number": inv_num_clean,
            "norm_inv_num": norm_inv_num,
            "created_at": created_at,
            "last_heartbeat": now,
        }

        conflicts: List[Dict[str, Any]] = []
        for other_sid, other in _sessions.items():
            if other_sid == session_id:
                continue
            if other.get("company_id") != int(company_id):
                continue
            
            # Check same or related scope (sales and debts are both customer/invoice related)
            same_or_related_scope = (other.get("scope") == scope) or (
                other.get("scope") in ("sales", "debts") and scope in ("sales", "debts")
            )
            if not same_or_related_scope:
                continue

            is_same_user = other.get("user_id") == int(user_id)
            has_conflict = False
            conflict_reason = ""

            other_res_id = other.get("resource_id", "")
            other_cust_id = other.get("customer_id", "")
            other_norm_cust = other.get("norm_customer_name", "")
            other_cust_name = other.get("customer_name") or cust_name_clean
            other_inv_num = other.get("invoice_number", "")
            other_norm_inv = other.get("norm_inv_num", "")
            other_user_name = other.get("user_name", "Người dùng khác")

            # Check 1: Sửa cùng một hóa đơn (cùng resource_id / sale_id)
            if res_id and other_res_id and res_id == other_res_id:
                has_conflict = True
                inv_label = f" (Số HĐ: {other_inv_num})" if other_inv_num else ""
                if is_same_user:
                    conflict_reason = f"Bạn đang mở sửa hóa đơn này{inv_label} ở một tab hoặc cửa sổ khác"
                else:
                    conflict_reason = f"đang mở sửa hóa đơn này{inv_label}"

            # Check 2: Cùng một khách hàng theo customer_id
            elif cust_id and other_cust_id and cust_id == other_cust_id:
                has_conflict = True
                target_name = other_cust_name or f"mã {cust_id}"
                extra_inv = f" (HĐ: {other_inv_num})" if other_inv_num else ""
                if is_same_user:
                    conflict_reason = f"Bạn đang mở nhập hóa đơn cho khách hàng {target_name}{extra_inv} ở một tab khác"
                else:
                    conflict_reason = f"đang nhập hóa đơn của khách hàng {target_name}{extra_inv}"

            # Check 3: Khớp tên khách hàng (nếu một trong hai bên chưa có customer_id nhưng đã gõ tên)
            elif norm_cust_name and other_norm_cust and len(norm_cust_name) >= 3 and (
                norm_cust_name == other_norm_cust
                or norm_cust_name in other_norm_cust
                or other_norm_cust in norm_cust_name
            ):
                has_conflict = True
                target_name = other_cust_name or cust_name_clean
                extra_inv = f" (HĐ: {other_inv_num})" if other_inv_num else ""
                if is_same_user:
                    conflict_reason = f"Bạn đang mở nhập hóa đơn cho khách hàng {target_name}{extra_inv} ở một tab khác"
                else:
                    conflict_reason = f"đang nhập hóa đơn của khách hàng {target_name}{extra_inv}"

            # Check 4: Cùng số hóa đơn / STT nhập tay
            elif norm_inv_num and other_norm_inv and norm_inv_num == other_norm_inv:
                has_conflict = True
                if is_same_user:
                    conflict_reason = f"Bạn đang nhập số hóa đơn {other_inv_num} ở tab khác"
                else:
                    conflict_reason = f"đang nhập cùng số hóa đơn {other_inv_num}"

            if has_conflict:
                conflicts.append({
                    "session_id": other_sid,
                    "user_id": other.get("user_id"),
                    "user_name": other_user_name,
                    "is_same_user": is_same_user,
                    "resource_id": other_res_id,
                    "customer_id": other_cust_id,
                    "customer_name": other_cust_name,
                    "invoice_number": other_inv_num,
                    "reason": conflict_reason,
                    "duration_sec": max(1, int(now - other.get("created_at", now))),
                })

        message = ""
        if conflicts:
            # Build detailed warning
            different_user_conflicts = [c for c in conflicts if not c["is_same_user"]]
            if different_user_conflicts:
                reasons_list = []
                for c in different_user_conflicts:
                    reasons_list.append(f"{c['user_name']} {c['reason']}")
                message = "Cảnh báo: " + "; ".join(reasons_list) + "! Vui lòng kiểm tra để tránh nhập đè dữ liệu."
            else:
                message = "Lưu ý: " + conflicts[0]["reason"] + "."

        return {
            "ok": True,
            "conflict": bool(conflicts),
            "conflicts": conflicts,
            "message": message,
        }


def release_session(session_id: str) -> None:
    """Release an editing session explicitly when modal is closed or saved."""
    if not session_id:
        return
    with _lock:
        _sessions.pop(session_id, None)


def get_active_sessions_for_company(company_id: int, scope: str = "sales") -> List[Dict[str, Any]]:
    """Return all active editing sessions for a company and scope (e.g. to display on tables)."""
    now = time.time()
    with _lock:
        _purge_expired_locked(now)
        results = [
            {
                "session_id": sid,
                "user_id": data.get("user_id"),
                "user_name": data.get("user_name"),
                "resource_id": data.get("resource_id"),
                "customer_id": data.get("customer_id"),
                "customer_name": data.get("customer_name"),
                "invoice_number": data.get("invoice_number"),
                "duration_sec": max(1, int(now - data.get("created_at", now))),
            }
            for sid, data in _sessions.items()
            if data.get("company_id") == int(company_id) and data.get("scope") == scope
        ]
    return results
