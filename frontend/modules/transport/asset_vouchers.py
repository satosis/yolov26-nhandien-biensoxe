from __future__ import annotations

from datetime import date
from typing import Any

from backend.services.database import db

VOUCHER_KINDS = ("revalue", "transfer", "decrease", "inventory")
BOOK_SOURCES = {"tscd": ("vehicle", "asset"), "ccdc": ("ccdc",)}
KIND_LABELS = {
    "tscd": {"revalue": "Đánh giá lại", "transfer": "Điều chuyển", "decrease": "Ghi giảm", "inventory": "Kiểm kê"},
    "ccdc": {"revalue": "Điều chỉnh", "transfer": "Điều chuyển", "decrease": "Ghi giảm", "inventory": "Kiểm kê"},
}
VOUCHER_PREFIX = {
    ("tscd", "revalue"): "DGLTS", ("tscd", "transfer"): "DCTS", ("tscd", "decrease"): "GGTS", ("tscd", "inventory"): "KKTS",
    ("ccdc", "revalue"): "DCCC", ("ccdc", "transfer"): "DCHCC", ("ccdc", "decrease"): "GGCC", ("ccdc", "inventory"): "KKCC",
}

_VOUCHER_TABLE = "public.transport_asset_vouchers"
def load_vouchers(company_id: int, book: str, kind: str | None = None) -> list[dict]:
    if not company_id or int(company_id) <= 0:
        return []
    sql = f"SELECT * FROM {_VOUCHER_TABLE} WHERE company_id = %s AND book = %s"
    params: list[Any] = [int(company_id), str(book)]
    if kind:
        sql += " AND kind = %s"
        params.append(str(kind))
    sql += " ORDER BY voucher_date ASC, id ASC"
    rows = db._execute_query(sql, tuple(params), fetchall=True, commit=False) or []
    return [dict(r) for r in rows]


def vouchers_by_item(company_id: int, book: str) -> dict[tuple[str, int], list[dict]]:
    all_v = load_vouchers(company_id, book)
    by_item: dict[tuple[str, int], list[dict]] = {}
    for v in all_v:
        key = (str(v["item_source"]), int(v["item_id"]))
        by_item.setdefault(key, []).append(v)
    return by_item


def _item_exists(cur, company_id: int, item_source: str, item_id: int) -> bool:
    cid = int(company_id)
    iid = int(item_id)
    if item_source == "vehicle":
        cur.execute("SELECT id FROM public.plate_whitelist WHERE id = %s AND company_id = %s", (iid, cid))
        return bool(cur.fetchone())
    if item_source == "asset":
        cur.execute("SELECT id FROM public.enterprise_assets WHERE id = %s AND company_id = %s", (iid, cid))
        return bool(cur.fetchone())
    if item_source == "ccdc":
        cur.execute("SELECT id FROM public.transport_prepaid_expenses WHERE id = %s AND company_id = %s", (iid, cid))
        return bool(cur.fetchone())
    return False


def parse_voucher_form(book: str, kind: str, form: dict) -> tuple[dict | None, str | None]:
    if book not in BOOK_SOURCES or kind not in VOUCHER_KINDS:
        return None, "Loại chứng từ không hợp lệ."

    raw_date = str(form.get("voucher_date") or "").strip()
    if not raw_date:
        return None, "Ngày chứng từ không hợp lệ."
    try:
        voucher_date = date.fromisoformat(raw_date[:10])
    except Exception:
        return None, "Ngày chứng từ không hợp lệ."

    item_err = "Vui lòng chọn tài sản." if book == "tscd" else "Vui lòng chọn công cụ dụng cụ."
    raw_item = str(form.get("item") or "").strip()
    if ":" not in raw_item:
        return None, item_err
    parts = raw_item.split(":", 1)
    item_source, raw_id = parts[0].strip(), parts[1].strip()
    if item_source not in BOOK_SOURCES[book]:
        return None, item_err
    try:
        item_id = int(raw_id)
    except Exception:
        return None, item_err

    voucher_no = str(form.get("voucher_no") or "").strip()[:50]
    reason = str(form.get("reason") or "").strip()[:255]
    note = str(form.get("note") or "").strip()[:500]

    new_cost: int | None = None
    new_months: int | None = None
    from_dept: str | None = None
    to_dept: str | None = None
    book_qty: float | None = None
    actual_qty: float | None = None
    condition: str | None = None

    if kind == "revalue":
        raw_cost = str(form.get("new_cost") or "").replace(".", "").replace(",", "").replace(" ", "").strip()
        try:
            new_cost = int(raw_cost)
            if new_cost <= 0:
                return None, "Giá trị sau điều chỉnh phải lớn hơn 0."
        except Exception:
            return None, "Giá trị sau điều chỉnh phải lớn hơn 0."

        raw_months = str(form.get("new_months") or "").strip()
        try:
            new_months = int(raw_months)
            if not (1 <= new_months <= 600):
                return None, "Số tháng còn lại phải từ 1 đến 600."
        except Exception:
            return None, "Số tháng còn lại phải từ 1 đến 600."

    elif kind == "transfer":
        to_dept = str(form.get("to_dept") or "").strip()[:255]
        if not to_dept:
            return None, "Vui lòng nhập đơn vị nhận."
        raw_from = str(form.get("from_dept") or "").strip()[:255]
        from_dept = raw_from if raw_from else None

    elif kind == "inventory":
        def _parse_qty(val: Any) -> float:
            s = str(val if val is not None else "").strip()
            if not s:
                return 1.0
            s = s.replace(",", ".")
            v = float(s)
            if v < 0:
                raise ValueError()
            return v

        try:
            book_qty = _parse_qty(form.get("book_qty"))
            actual_qty = _parse_qty(form.get("actual_qty"))
        except Exception:
            return None, "Số lượng kiểm kê không hợp lệ."

        raw_cond = str(form.get("condition") or "").strip()[:100]
        condition = raw_cond if raw_cond else None

    values = {
        "voucher_no": voucher_no,
        "voucher_date": voucher_date,
        "item_source": item_source,
        "item_id": item_id,
        "reason": reason,
        "note": note,
        "new_cost": new_cost,
        "new_months": new_months,
        "from_dept": from_dept,
        "to_dept": to_dept,
        "book_qty": book_qty,
        "actual_qty": actual_qty,
        "condition": condition,
    }
    return values, None


def create_voucher(company_id: int, book: str, kind: str, values: dict) -> tuple[bool, str]:
    cid = int(company_id)
    with db.transaction() as cur:
        if not _item_exists(cur, cid, values["item_source"], values["item_id"]):
            return False, "Không tìm thấy tài sản."

        if kind == "decrease":
            cur.execute("""
                SELECT id FROM public.transport_asset_vouchers
                WHERE company_id = %s AND book = %s AND item_source = %s AND item_id = %s AND kind = 'decrease'
            """, (cid, book, values["item_source"], values["item_id"]))
            if cur.fetchone():
                return False, "Tài sản này đã được ghi giảm."

        voucher_no = values.get("voucher_no") or ""
        if not voucher_no:
            cur.execute("""
                SELECT count(*) AS cnt FROM public.transport_asset_vouchers
                WHERE company_id = %s AND book = %s AND kind = %s
            """, (cid, book, kind))
            row = cur.fetchone()
            cnt = int(row["cnt"]) if row else 0
            n = cnt + 1
            prefix = VOUCHER_PREFIX.get((book, kind), "CT")
            voucher_no = f"{prefix}{n:05d}"

        cur.execute("""
            INSERT INTO public.transport_asset_vouchers
                (company_id, book, kind, voucher_no, voucher_date, item_source, item_id,
                 reason, new_cost, new_months, from_dept, to_dept, book_qty, actual_qty, condition, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            cid, book, kind, voucher_no, values["voucher_date"], values["item_source"], values["item_id"],
            values.get("reason") or "", values.get("new_cost"), values.get("new_months"),
            values.get("from_dept"), values.get("to_dept"), values.get("book_qty"), values.get("actual_qty"),
            values.get("condition"), values.get("note") or ""
        ))

    label = KIND_LABELS.get(book, {}).get(kind, kind).lower()
    return True, f"Đã thêm chứng từ {label}."


def get_voucher_kind(company_id: int, book: str, voucher_id: int) -> str | None:
    row = db._execute_query(
        f"SELECT kind FROM {_VOUCHER_TABLE} WHERE id = %s AND company_id = %s AND book = %s",
        (int(voucher_id), int(company_id), str(book)),
        fetch=True,
        commit=False,
    )
    return str(row["kind"]) if row and row.get("kind") else None


def update_voucher(company_id: int, book: str, voucher_id: int, values: dict) -> tuple[bool, str]:
    cid = int(company_id)
    vid = int(voucher_id)
    with db.transaction() as cur:
        cur.execute(
            f"SELECT kind, voucher_no FROM {_VOUCHER_TABLE} WHERE id = %s AND company_id = %s AND book = %s",
            (vid, cid, book),
        )
        existing = cur.fetchone()
        if not existing:
            return False, "Không tìm thấy chứng từ."

        kind = existing["kind"]
        if not _item_exists(cur, cid, values["item_source"], values["item_id"]):
            return False, "Không tìm thấy tài sản."

        if kind == "decrease":
            cur.execute(f"""
                SELECT id FROM {_VOUCHER_TABLE}
                WHERE company_id = %s AND book = %s AND item_source = %s AND item_id = %s AND kind = 'decrease' AND id != %s
            """, (cid, book, values["item_source"], values["item_id"], vid))
            if cur.fetchone():
                return False, "Tài sản này đã được ghi giảm."

        voucher_no = values.get("voucher_no") or existing["voucher_no"]

        cur.execute(f"""
            UPDATE {_VOUCHER_TABLE}
            SET voucher_no = %s,
                voucher_date = %s,
                item_source = %s,
                item_id = %s,
                reason = %s,
                new_cost = %s,
                new_months = %s,
                from_dept = %s,
                to_dept = %s,
                book_qty = %s,
                actual_qty = %s,
                condition = %s,
                note = %s,
                updated_at = now()
            WHERE id = %s AND company_id = %s AND book = %s
        """, (
            voucher_no, values["voucher_date"], values["item_source"], values["item_id"],
            values.get("reason") or "", values.get("new_cost"), values.get("new_months"),
            values.get("from_dept"), values.get("to_dept"), values.get("book_qty"), values.get("actual_qty"),
            values.get("condition"), values.get("note") or "",
            vid, cid, book
        ))

    return True, "Đã lưu chứng từ."


def delete_voucher(company_id: int, book: str, voucher_id: int) -> tuple[bool, str]:
    cid = int(company_id)
    vid = int(voucher_id)
    with db.transaction() as cur:
        cur.execute(
            f"DELETE FROM {_VOUCHER_TABLE} WHERE id = %s AND company_id = %s AND book = %s RETURNING id",
            (vid, cid, book),
        )
        row = cur.fetchone()
        if not row:
            return False, "Không tìm thấy chứng từ."
    return True, "Đã xoá chứng từ."


def delete_item_vouchers(cur, company_id: int, book: str, item_source: str, item_id: int) -> None:
    cur.execute(
        f"DELETE FROM {_VOUCHER_TABLE} WHERE company_id = %s AND book = %s AND item_source = %s AND item_id = %s",
        (int(company_id), str(book), str(item_source), int(item_id)),
    )


def voucher_edit_payload(v: dict) -> dict:
    v_date = v.get("voucher_date")
    if hasattr(v_date, "isoformat"):
        date_str = v_date.isoformat()[:10]
    elif v_date:
        date_str = str(v_date)[:10]
    else:
        date_str = ""

    new_cost = v.get("new_cost")
    if new_cost is not None:
        new_cost = int(round(float(new_cost)))

    book_qty = v.get("book_qty")
    if book_qty is not None:
        book_qty = float(book_qty)

    actual_qty = v.get("actual_qty")
    if actual_qty is not None:
        actual_qty = float(actual_qty)

    return {
        "id": int(v["id"]),
        "kind": str(v["kind"]),
        "voucher_no": str(v.get("voucher_no") or ""),
        "voucher_date": date_str,
        "item": f"{v['item_source']}:{v['item_id']}",
        "reason": str(v.get("reason") or ""),
        "note": str(v.get("note") or ""),
        "new_cost": new_cost,
        "new_months": int(v["new_months"]) if v.get("new_months") is not None else None,
        "from_dept": str(v.get("from_dept") or "") if v.get("from_dept") else None,
        "to_dept": str(v.get("to_dept") or "") if v.get("to_dept") else None,
        "book_qty": book_qty,
        "actual_qty": actual_qty,
        "condition": str(v.get("condition") or "") if v.get("condition") else None,
    }
