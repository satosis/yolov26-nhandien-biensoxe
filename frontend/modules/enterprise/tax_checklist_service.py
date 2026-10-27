"""Service for Tax and Accounting Work Checklist & Dashboard."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from backend.services.database import db

CHECKLIST_GROUPS = [
    {
        "id": "gtgt",
        "name": "THUẾ GTGT",
        "bg_color": "#e0f2fe",
        "border_color": "#7dd3fc",
        "header_color": "#0369a1",
        "badge_color": "primary",
        "items": [
            {"key": "gtgt_tokhai", "name": "Tờ khai GTGT", "short": "Tờ khai GTGT"},
            {"key": "gtgt_bk_mua", "name": "BK mua vào + HĐ đầu vào", "short": "BK mua vào + HĐ"},
            {"key": "gtgt_bk_ban", "name": "BK bán ra + HĐ đầu ra", "short": "BK bán ra + HĐ"},
            {"key": "gtgt_giay_nop", "name": "Giấy nộp tiền (nếu có)", "short": "Giấy nộp tiền"},
        ]
    },
    {
        "id": "tncn",
        "name": "THUẾ TNCN & HỒ SƠ NHÂN SỰ",
        "bg_color": "#fef3c7",
        "border_color": "#fcd34d",
        "header_color": "#b45309",
        "badge_color": "warning",
        "items": [
            {"key": "tncn_tokhai", "name": "Tờ khai TNCN quý", "short": "Tờ khai quý"},
            {"key": "tncn_bang_th", "name": "Bảng tổng hợp số liệu", "short": "Bảng tổng hợp"},
            {"key": "tncn_quyet_toan", "name": "Quyết toán TNCN", "short": "Quyết toán"},
            {"key": "tncn_khau_tru", "name": "Chứng từ khấu trừ & ủy quyền", "short": "CT khấu trừ"},
            {"key": "tncn_hs_luong", "name": "Hồ sơ lương", "short": "Hồ sơ lương"},
            {"key": "tncn_hs_bhxh", "name": "Hồ sơ BHXH", "short": "Hồ sơ BHXH"},
            {"key": "tncn_hs_lao_dong", "name": "Hồ sơ lao động", "short": "Hồ sơ LĐ"},
        ]
    },
    {
        "id": "tndn",
        "name": "THUẾ TNDN",
        "bg_color": "#dcfce7",
        "border_color": "#86efac",
        "header_color": "#15803d",
        "badge_color": "success",
        "items": [
            {"key": "tndn_tam_nop", "name": "Tạm nộp TNDN", "short": "Tạm nộp TNDN"},
            {"key": "tndn_bctc", "name": "BCTC", "short": "BCTC"},
            {"key": "tndn_qtt", "name": "Tờ khai QTT TNDN", "short": "Tờ khai QTT"},
            {"key": "tndn_bang_tinh", "name": "Bảng tính thuế & giải trình", "short": "Bảng tính thuế"},
        ]
    },
    {
        "id": "sao_ke",
        "name": "SAO KÊ & SỔ QUỸ",
        "bg_color": "#ffedd5",
        "border_color": "#fdba74",
        "header_color": "#c2410c",
        "badge_color": "orange",
        "items": [
            {"key": "sq_sao_ke", "name": "Sao kê ngân hàng", "short": "Sao kê NH"},
            {"key": "sq_so_quy", "name": "Sổ quỹ + phiếu thu/chi", "short": "Sổ quỹ/thu chi"},
            {"key": "sq_hs_thanh_toan", "name": "Hồ sơ thanh toán", "short": "HS thanh toán"},
        ]
    },
    {
        "id": "hop_dong",
        "name": "HỢP ĐỒNG & GIAO NHẬN",
        "bg_color": "#cffafe",
        "border_color": "#67e8f9",
        "header_color": "#0e7490",
        "badge_color": "info",
        "items": [
            {"key": "hd_hop_dong", "name": "Hợp đồng mua vào/bán ra", "short": "Hợp đồng mua/bán"},
            {"key": "hd_bbgh", "name": "Biên bản giao hàng/PKK", "short": "BB giao hàng/PKK"},
            {"key": "hd_hoa_don", "name": "Hóa đơn liên quan", "short": "Hóa đơn liên quan"},
        ]
    },
    {
        "id": "so_sach",
        "name": "SỔ SÁCH KẾ TOÁN",
        "bg_color": "#f3e8ff",
        "border_color": "#d8b4fe",
        "header_color": "#7e22ce",
        "badge_color": "purple",
        "items": [
            {"key": "ss_cdps", "name": "Bảng cân đối phát sinh", "short": "Bảng CĐPS"},
            {"key": "ss_nhat_ky", "name": "Sổ nhật ký chung", "short": "Sổ nhật ký chung"},
            {"key": "ss_chi_tiet", "name": "Sổ chi tiết tài khoản", "short": "Sổ chi tiết TK"},
            {"key": "ss_nx_ton", "name": "Bảng NX Tồn kho", "short": "Bảng NX tồn kho"},
            {"key": "ss_khau_hao", "name": "Bảng khấu hao TSCĐ", "short": "Khấu hao TSCĐ"},
        ]
    }
]

# Flatten all items for quick index lookup
ALL_ITEMS = []
ITEM_GROUP_MAP = {}
for g in CHECKLIST_GROUPS:
    for item in g["items"]:
        ALL_ITEMS.append(item)
        ITEM_GROUP_MAP[item["key"]] = g["id"]

STATUS_CYCLE = ["not_done", "in_progress", "done", "na"]


def get_available_periods() -> List[Dict[str, str]]:
    """Return selectable quarterly periods."""
    return [
        {"key": "2026-Q4", "label": "Quý 4/2026", "date_range": "01/10/2026 - 31/12/2026"},
        {"key": "2026-Q3", "label": "Quý 3/2026", "date_range": "01/07/2026 - 30/09/2026"},
        {"key": "2026-Q2", "label": "Quý 2/2026", "date_range": "01/04/2026 - 30/06/2026"},
        {"key": "2026-Q1", "label": "Quý 1/2026", "date_range": "01/01/2026 - 31/03/2026"},
        {"key": "2025-Q4", "label": "Quý 4/2025", "date_range": "01/10/2025 - 31/12/2025"},
    ]


def get_checklist_matrix(period: str, q_search: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve complete checklist data for matrix table and dashboard."""
    query = """
        SELECT id, code, name, tax_code, contact_info, note, is_active
        FROM tax_checklist_clients
        WHERE is_active = TRUE
    """
    params = []
    if q_search:
        query += " AND (code ILIKE %s OR name ILIKE %s OR tax_code ILIKE %s)"
        pattern = f"%{q_search}%"
        params.extend([pattern, pattern, pattern])

    query += " ORDER BY code ASC, id ASC"
    clients_rows = db.execute_query(query, tuple(params) if params else None, fetchall=True) or []

    if not clients_rows:
        return {
            "clients": [],
            "column_totals": {},
            "overall_stats": {
                "total_clients": 0,
                "completed_clients": 0,
                "in_progress_clients": 0,
                "overdue_clients": 0,
                "overall_progress": 0,
            },
            "groups": CHECKLIST_GROUPS,
            "all_items": ALL_ITEMS,
        }

    # Fetch all checklist entries for this period
    entries = db.execute_query(
        "SELECT client_id, item_key, status, note FROM tax_checklist_entries WHERE period = %s",
        (period,),
        fetchall=True
    ) or []

    # Map entries: (client_id, item_key) -> status
    entry_map = {(e["client_id"], e["item_key"]): e["status"] for e in entries}

    # Fetch period notes
    notes = db.execute_query(
        "SELECT client_id, general_note FROM tax_checklist_notes WHERE period = %s",
        (period,),
        fetchall=True
    ) or []
    note_map = {n["client_id"]: n["general_note"] for n in notes}

    # Structure client matrix
    total_items_count = len(ALL_ITEMS)
    processed_clients = []

    # Counters for bottom summary rows
    col_done_counts = {item["key"]: 0 for item in ALL_ITEMS}
    col_applicable_counts = {item["key"]: 0 for item in ALL_ITEMS}

    completed_clients_count = 0
    in_progress_clients_count = 0
    overdue_clients_count = 0

    for c in clients_rows:
        c_id = c["id"]
        client_statuses = {}
        done_cnt = 0
        applicable_cnt = 0
        has_not_done = False
        has_in_progress = False

        # Group-level aggregate statuses for high-level dashboard
        group_summaries = {}
        for g in CHECKLIST_GROUPS:
            g_items = g["items"]
            g_done = 0
            g_not_done = 0
            g_in_progress = 0
            g_na = 0

            for it in g_items:
                k = it["key"]
                st = entry_map.get((c_id, k), "not_done")
                client_statuses[k] = st

                if st == "done":
                    done_cnt += 1
                    applicable_cnt += 1
                    g_done += 1
                    col_done_counts[k] += 1
                    col_applicable_counts[k] += 1
                elif st == "in_progress":
                    applicable_cnt += 1
                    g_in_progress += 1
                    col_applicable_counts[k] += 1
                    has_in_progress = True
                elif st == "na":
                    g_na += 1
                else:  # not_done
                    applicable_cnt += 1
                    g_not_done += 1
                    col_applicable_counts[k] += 1
                    has_not_done = True

            # Determine group overall badge
            if g_done == len(g_items):
                group_st = "Done"
                badge_type = "success"
            elif g_na == len(g_items):
                group_st = "N/A"
                badge_type = "secondary"
            elif g_not_done == len(g_items):
                group_st = "Chưa làm"
                badge_type = "danger"
            elif g_in_progress > 0 or (g_done > 0 and g_not_done > 0):
                group_st = "Đang làm"
                badge_type = "warning"
            elif g_done > 0:
                group_st = "Đang làm"
                badge_type = "warning"
            else:
                group_st = "Chưa làm"
                badge_type = "danger"

            group_summaries[g["id"]] = {
                "label": group_st,
                "badge_type": badge_type,
                "done": g_done,
                "total": len(g_items),
            }

        pct = round((done_cnt / total_items_count) * 100) if total_items_count > 0 else 0

        # Classification for stats
        if pct >= 80:
            completed_clients_count += 1
        elif pct >= 40:
            in_progress_clients_count += 1
        else:
            overdue_clients_count += 1

        processed_clients.append({
            "id": c_id,
            "code": c["code"],
            "name": c["name"],
            "tax_code": c.get("tax_code") or "",
            "contact_info": c.get("contact_info") or "",
            "note": note_map.get(c_id, c.get("note") or ""),
            "statuses": client_statuses,
            "group_summaries": group_summaries,
            "done_count": done_cnt,
            "total_items": total_items_count,
            "percentage": pct,
        })

    # Calculate column percentages for bottom summary
    total_clients_len = len(clients_rows)
    column_totals = {}
    for it in ALL_ITEMS:
        k = it["key"]
        cnt = col_done_counts[k]
        pct = round((cnt / total_clients_len) * 100) if total_clients_len > 0 else 0
        column_totals[k] = {
            "done_count": cnt,
            "total": total_clients_len,
            "pct": pct,
        }

    overall_progress = round(
        sum(c["percentage"] for c in processed_clients) / total_clients_len
    ) if total_clients_len > 0 else 0

    return {
        "clients": processed_clients,
        "column_totals": column_totals,
        "overall_stats": {
            "total_clients": total_clients_len,
            "completed_clients": completed_clients_count,
            "in_progress_clients": in_progress_clients_count,
            "overdue_clients": overdue_clients_count,
            "overall_progress": overall_progress,
        },
        "groups": CHECKLIST_GROUPS,
        "all_items": ALL_ITEMS,
    }


def cycle_item_status(client_id: int, period: str, item_key: str) -> Dict[str, Any]:
    """Toggle status to next in cycle: not_done -> in_progress -> done -> na -> not_done."""
    current = db.execute_query(
        "SELECT status FROM tax_checklist_entries WHERE client_id = %s AND period = %s AND item_key = %s",
        (client_id, period, item_key),
        fetch=True
    )

    current_status = current["status"] if current else "not_done"
    try:
        idx = STATUS_CYCLE.index(current_status)
        next_status = STATUS_CYCLE[(idx + 1) % len(STATUS_CYCLE)]
    except ValueError:
        next_status = "done"

    db.execute_query("""
        INSERT INTO tax_checklist_entries (client_id, period, item_key, status, updated_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (client_id, period, item_key)
        DO UPDATE SET status = EXCLUDED.status, updated_at = CURRENT_TIMESTAMP
    """, (client_id, period, item_key, next_status), commit=True)

    # Recalculate client stats
    entries = db.execute_query(
        "SELECT status FROM tax_checklist_entries WHERE client_id = %s AND period = %s",
        (client_id, period),
        fetchall=True
    ) or []

    done_cnt = sum(1 for e in entries if e["status"] == "done")
    total_cnt = len(ALL_ITEMS)
    pct = round((done_cnt / total_cnt) * 100) if total_cnt > 0 else 0

    return {
        "ok": True,
        "client_id": client_id,
        "item_key": item_key,
        "new_status": next_status,
        "done_count": done_cnt,
        "total_count": total_cnt,
        "percentage": pct,
    }


def cycle_group_status(client_id: int, period: str, group_id: str) -> Dict[str, Any]:
    """Toggle all items in a group to next status: not_done -> in_progress -> done -> not_done."""
    group = next((g for g in CHECKLIST_GROUPS if g["id"] == group_id), None)
    if not group:
        # Check if it's a single item key like 'tndn_bctc'
        res = cycle_item_status(client_id, period, group_id)
        st = res["new_status"]
        if st == "done":
            lbl, badge = "Done", "success"
        elif st == "in_progress":
            lbl, badge = "Đang làm", "warning"
        elif st == "na":
            lbl, badge = "N/A", "secondary"
        else:
            lbl, badge = "Chưa làm", "danger"
        res.update({"label": lbl, "badge_type": badge, "status": st, "group_id": group_id})
        return res

    items = group["items"]
    item_keys = [it["key"] for it in items]

    # Query current statuses
    entries = db.execute_query(
        "SELECT item_key, status FROM tax_checklist_entries WHERE client_id = %s AND period = %s AND item_key = ANY(%s)",
        (client_id, period, item_keys),
        fetchall=True
    ) or []
    status_map = {e["item_key"]: e["status"] for e in entries}

    done_count = sum(1 for k in item_keys if status_map.get(k) == "done")
    in_progress_count = sum(1 for k in item_keys if status_map.get(k) == "in_progress")

    if done_count == len(item_keys):
        next_status = "not_done"
        new_label = "Chưa làm"
        new_badge_type = "danger"
    elif in_progress_count > 0 or done_count > 0:
        next_status = "done"
        new_label = "Done"
        new_badge_type = "success"
    else:
        next_status = "in_progress"
        new_label = "Đang làm"
        new_badge_type = "warning"

    for k in item_keys:
        db.execute_query("""
            INSERT INTO tax_checklist_entries (client_id, period, item_key, status, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (client_id, period, item_key)
            DO UPDATE SET status = EXCLUDED.status, updated_at = CURRENT_TIMESTAMP
        """, (client_id, period, k, next_status), commit=True)

    all_client_entries = db.execute_query(
        "SELECT status FROM tax_checklist_entries WHERE client_id = %s AND period = %s",
        (client_id, period),
        fetchall=True
    ) or []
    total_done = sum(1 for e in all_client_entries if e["status"] == "done")
    total_cnt = len(ALL_ITEMS)
    pct = round((total_done / total_cnt) * 100) if total_cnt > 0 else 0

    return {
        "ok": True,
        "client_id": client_id,
        "group_id": group_id,
        "status": next_status,
        "label": new_label,
        "badge_type": new_badge_type,
        "done_count": total_done,
        "total_count": total_cnt,
        "percentage": pct,
    }


def set_item_status(client_id: int, period: str, item_key: str, status: str) -> Dict[str, Any]:
    """Explicitly set status."""
    if status not in STATUS_CYCLE:
        status = "not_done"

    db.execute_query("""
        INSERT INTO tax_checklist_entries (client_id, period, item_key, status, updated_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (client_id, period, item_key)
        DO UPDATE SET status = EXCLUDED.status, updated_at = CURRENT_TIMESTAMP
    """, (client_id, period, item_key, status), commit=True)

    return {"ok": True, "status": status}


def update_client_note(client_id: int, period: str, note: str) -> bool:
    """Save client's general note for period."""
    db.execute_query("""
        INSERT INTO tax_checklist_notes (client_id, period, general_note, updated_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (client_id, period)
        DO UPDATE SET general_note = EXCLUDED.general_note, updated_at = CURRENT_TIMESTAMP
    """, (client_id, period, note), commit=True)
    return True


def add_client(code: str, name: str, tax_code: str = "", contact: str = "", note: str = "") -> Dict[str, Any]:
    """Add a new client to checklist."""
    code = code.strip().upper()
    name = name.strip()
    if not code or not name:
        return {"ok": False, "message": "Mã và Tên khách hàng không được để trống"}

    existing = db.execute_query("SELECT id FROM tax_checklist_clients WHERE code = %s", (code,), fetch=True)
    if existing:
        return {"ok": False, "message": f"Mã khách hàng {code} đã tồn tại"}

    new_id = db.execute_query("""
        INSERT INTO tax_checklist_clients (code, name, tax_code, contact_info, note)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """, (code, name, tax_code, contact, note), return_id=True, commit=True)

    return {"ok": True, "id": new_id}


def get_urgent_tasks(period: str) -> List[Dict[str, Any]]:
    """Get tasks due today or urgent for period."""
    tasks = db.execute_query("""
        SELECT id, period, title, client_name, deadline, is_completed
        FROM tax_checklist_urgent_tasks
        WHERE period = %s
        ORDER BY is_completed ASC, id ASC
    """, (period,), fetchall=True) or []
    return tasks


def toggle_urgent_task(task_id: int) -> bool:
    """Toggle completion status of an urgent task."""
    db.execute_query("""
        UPDATE tax_checklist_urgent_tasks
        SET is_completed = NOT is_completed
        WHERE id = %s
    """, (task_id,), commit=True)
    return True


def add_urgent_task(period: str, title: str, client_name: str, deadline: str) -> bool:
    """Add an urgent task."""
    db.execute_query("""
        INSERT INTO tax_checklist_urgent_tasks (period, title, client_name, deadline)
        VALUES (%s, %s, %s, %s)
    """, (period, title, client_name, deadline), commit=True)
    return True


def get_notifications(period: str) -> List[Dict[str, Any]]:
    """Return reminders and warnings for the period."""
    return [
        {"title": "CÔNG TY TNHH TM VÀ DV PHƯƠNG NAM - Cần hoàn thiện tờ khai GTGT", "date": "23/10", "type": "warning"},
        {"title": "CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH - Cần bổ sung sao kê ngân hàng", "date": "23/10", "type": "warning"},
        {"title": "Đến hạn nộp tạm nộp TNDN quý 3/2026", "date": "20/10", "type": "danger"},
        {"title": "Cập nhật bảng lương TNCN tháng 10/2026", "date": "20/10", "type": "info"},
    ]
