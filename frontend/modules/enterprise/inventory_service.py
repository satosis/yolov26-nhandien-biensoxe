"""Sổ theo dõi tồn kho và nhật ký nhập xuất kho."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import io
import re
from typing import Any

import openpyxl

from backend.services.database import db
from frontend.modules.enterprise.sales_service import _folded, fold_text

SALE_WAREHOUSES = [("ĐV", "Đồng Văn"), ("TL", "Tề Lỗ")]  # value stored, full name
# "ĐV" and "Đồng Văn" (likewise "TL" / "Tề Lỗ") are the same warehouse however a person or a form wrote it.
_WAREHOUSE_ALIASES = {"dongvan": "dv", "dv": "dv", "telo": "tl", "tl": "tl"}


def normalize_unit(unit) -> str:
    """One spelling per unit, first letter capital: 'tấm', 'TẤM', ' Tấm ' -> 'Tấm'."""
    text = " ".join(str(unit or "").split())
    return text[:1].upper() + text[1:].lower()


def _warehouse_words_sql(column: str) -> str:
    """The warehouse as searchable words: 'ĐV' also reads 'dong van', 'TL' also 'te lo'."""
    folded = f"lower({_folded(column)})"
    return (f"(CASE WHEN replace({folded}, ' ', '') IN ('dv', 'dongvan') THEN 'dv dong van' "
            f"WHEN replace({folded}, ' ', '') IN ('tl', 'telo') THEN 'tl te lo' ELSE {folded} END)")


def warehouse_key(warehouse: str) -> str:
    compact = "".join(fold_text(warehouse or "").split())
    return _WAREHOUSE_ALIASES.get(compact, compact)


def name_order_key(name: str) -> list:
    """Alphabetical, with numbers compared as numbers: "U80" before "U100", "Ván be" after "U200"."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part)
            for part in re.findall(r"\d+|\D+", " ".join(fold_text(name or "").split()))]


def parse_quantity(val: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Parse số lượng tồn, nhập, xuất dạng '1.234,5' hoặc '1234.5', cho phép số âm."""
    if val is None or val == "":
        return default
    if isinstance(val, Decimal):
        return val
    if isinstance(val, (int, float)):
        return Decimal(str(val))
    s = str(val).strip().replace(" ", "")
    if not s:
        return default
    # Loại bỏ dấu chấm ngăn nghìn (VD: 1.234,5 -> 1234,5 hoặc 1.234.567)
    s = re.sub(r"\.(?=\d{3}(?:\.|$|,))", "", s)
    s = s.replace(",", ".")
    try:
        res = Decimal(s)
        if not res.is_finite():
            raise ValueError(f"Số lượng không hợp lệ: {val}")
        return res
    except Exception as exc:
        raise ValueError(f"Số lượng không hợp lệ: {val}") from exc


def list_warehouses(company_id: int) -> list[str]:
    """Danh sách kho hàng đã có trong công ty (cho datalist)."""
    rows = db._execute_query(
        """
        SELECT DISTINCT warehouse
        FROM public.inventory_items
        WHERE company_id = %s AND warehouse IS NOT NULL AND TRIM(warehouse) <> ''
        ORDER BY warehouse ASC
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []
    return [r["warehouse"] for r in rows if r.get("warehouse")]


def list_units(company_id: int) -> list[str]:
    """Danh sách đơn vị tính đã dùng trong công ty (cho datalist)."""
    rows = db._execute_query(
        """
        SELECT DISTINCT unit
        FROM public.inventory_items
        WHERE company_id = %s AND unit IS NOT NULL AND TRIM(unit) <> ''
        ORDER BY unit ASC
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []
    return sorted({normalize_unit(r["unit"]) for r in rows if r.get("unit")}, key=fold_text)


def list_all_items(company_id: int) -> list[dict]:
    """Tất cả mặt hàng của công ty để hiển thị trong select dropdown của phiếu."""
    rows = db._execute_query(
        """
        SELECT i.id, i.warehouse, i.code, i.name, i.unit,
               i.opening_qty + COALESCE((SELECT SUM(CASE WHEN m.kind = 'in' THEN m.quantity ELSE -m.quantity END)
                                         FROM public.inventory_movements m WHERE m.item_id = i.id), 0) AS closing
        FROM public.inventory_items i
        WHERE i.company_id = %s
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []
    alias_rows = db._execute_query(
        """
        SELECT item_id, name
        FROM public.inventory_item_aliases
        WHERE company_id = %s
        ORDER BY id ASC
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []
    aliases_by_item: dict[int, list[str]] = {}
    for a in alias_rows:
        iid = a["item_id"]
        aname = str(a.get("name") or "").strip()
        if aname:
            aliases_by_item.setdefault(iid, []).append(aname)

    for r in rows:
        r["unit"] = normalize_unit(r["unit"])
        r["closing"] = float(r.get("closing") or 0)
        r["aliases"] = aliases_by_item.get(r["id"], [])
    return sorted(rows, key=lambda r: (name_order_key(r["name"]), warehouse_key(r["warehouse"]), r["id"]))


def stock_book(
    company_id: int,
    *,
    q: str = "",
    warehouse: str = "",
    date_from: str | date | None = None,
    date_to: str | date | None = None,
    page: int = 1,
    per_page: int = 100,
) -> tuple[list[dict], int]:
    """Sổ theo dõi tồn kho: opening, qty_in, qty_out, closing, in_stock."""
    clean_date_from = str(date_from).strip()[:10] if date_from else None
    clean_date_to = str(date_to).strip()[:10] if date_to else None

    where_filters = ["i.company_id = %s"]
    where_params: list[Any] = [company_id]

    if (warehouse or "").strip() == "__none__":
        where_filters.append("btrim(COALESCE(i.warehouse, '')) = ''")  # items nobody put in a warehouse
    elif warehouse and warehouse.strip():
        where_filters.append("i.warehouse = %s")
        where_params.append(warehouse.strip())

    if q and q.strip():
        words = fold_text(q).strip().split()
        for w in words:
            pattern = f"%{w}%"
            where_filters.append(
                f"""(
                    lower({_folded('i.code')}) ILIKE %s OR
                    lower({_folded('i.name')}) ILIKE %s OR
                    {_warehouse_words_sql('i.warehouse')} ILIKE %s OR
                    lower({_folded('i.unit')}) ILIKE %s OR
                    lower({_folded('i.note')}) ILIKE %s
                )"""
            )
            where_params.extend([pattern, pattern, pattern, pattern, pattern])

    where_sql = " AND ".join(where_filters)

    count_row = db._execute_query(
        f"SELECT COUNT(*) AS total FROM public.inventory_items i WHERE {where_sql}",
        tuple(where_params),
        fetch=True,
        commit=False,
    )
    total = int(count_row["total"]) if count_row and count_row.get("total") is not None else 0
    if total == 0:
        return [], 0

    # Subqueries for opening, in, out
    select_params: list[Any] = []
    if clean_date_from:
        opening_expr = """i.opening_qty + COALESCE((
            SELECT SUM(CASE WHEN m.kind = 'in' THEN m.quantity WHEN m.kind = 'out' THEN -m.quantity ELSE 0 END)
            FROM public.inventory_movements m
            WHERE m.item_id = i.id AND m.movement_date < %s
        ), 0)"""
        select_params.append(clean_date_from)
    else:
        opening_expr = "i.opening_qty"

    in_conds = ["m.item_id = i.id", "m.kind = 'in'"]
    if clean_date_from:
        in_conds.append("m.movement_date >= %s")
        select_params.append(clean_date_from)
    if clean_date_to:
        in_conds.append("m.movement_date <= %s")
        select_params.append(clean_date_to)
    qty_in_expr = f"COALESCE((SELECT SUM(m.quantity) FROM public.inventory_movements m WHERE {' AND '.join(in_conds)}), 0)"

    out_conds = ["m.item_id = i.id", "m.kind = 'out'"]
    if clean_date_from:
        out_conds.append("m.movement_date >= %s")
        select_params.append(clean_date_from)
    if clean_date_to:
        out_conds.append("m.movement_date <= %s")
        select_params.append(clean_date_to)
    qty_out_expr = f"COALESCE((SELECT SUM(m.quantity) FROM public.inventory_movements m WHERE {' AND '.join(out_conds)}), 0)"

    limit = max(1, per_page)
    offset = max(0, (page - 1) * limit)

    query = f"""
        SELECT
            i.id,
            i.company_id,
            i.warehouse,
            i.code,
            i.name,
            i.unit,
            i.note,
            i.opening_qty,
            {opening_expr} AS opening,
            {qty_in_expr} AS qty_in,
            {qty_out_expr} AS qty_out
        FROM public.inventory_items i
        WHERE {where_sql}
    """
    full_params = select_params + where_params
    rows = db._execute_query(query, tuple(full_params), fetchall=True, commit=False) or []
    # Item names in alphabetical order with numbers read as numbers (U80 before U100), then the page is cut.
    rows.sort(key=lambda r: (name_order_key(r["name"]), warehouse_key(r["warehouse"]), r["id"]))
    rows = rows[offset:offset + limit]

    for r in rows:
        opening = Decimal(str(r["opening"] or 0))
        qty_in = Decimal(str(r["qty_in"] or 0))
        qty_out = Decimal(str(r["qty_out"] or 0))
        closing = opening + qty_in - qty_out
        r["opening"] = opening
        r["qty_in"] = qty_in
        r["qty_out"] = qty_out
        r["closing"] = closing
        r["in_stock"] = closing > 0
        r["wh_key"] = warehouse_key(r["warehouse"])
        r["unit"] = normalize_unit(r["unit"])

    return rows, total


def journal(
    company_id: int,
    *,
    q: str = "",
    warehouse: str = "",
    kind: str = "",
    date_from: str | date | None = None,
    date_to: str | date | None = None,
    page: int = 1,
    per_page: int = 100,
) -> tuple[list[dict], int]:
    """Nhật ký nhập xuất: danh sách phiếu, mới nhất trước."""
    clean_date_from = str(date_from).strip()[:10] if date_from else None
    clean_date_to = str(date_to).strip()[:10] if date_to else None

    where_filters = ["m.company_id = %s"]
    where_params: list[Any] = [company_id]

    if (warehouse or "").strip() == "__none__":
        where_filters.append("btrim(COALESCE(i.warehouse, '')) = ''")  # items nobody put in a warehouse
    elif warehouse and warehouse.strip():
        where_filters.append("i.warehouse = %s")
        where_params.append(warehouse.strip())

    clean_kind = (kind or "").strip().lower()
    if clean_kind in ("in", "out"):
        where_filters.append("m.kind = %s")
        where_params.append(clean_kind)

    if clean_date_from:
        where_filters.append("m.movement_date >= %s")
        where_params.append(clean_date_from)
    if clean_date_to:
        where_filters.append("m.movement_date <= %s")
        where_params.append(clean_date_to)

    if q and q.strip():
        words = fold_text(q).strip().split()
        for w in words:
            pattern = f"%{w}%"
            where_filters.append(
                f"""(
                    lower({_folded('i.code')}) ILIKE %s OR
                    lower({_folded('i.name')}) ILIKE %s OR
                    {_warehouse_words_sql('i.warehouse')} ILIKE %s OR
                    lower({_folded('i.unit')}) ILIKE %s OR
                    lower({_folded('i.note')}) ILIKE %s OR
                    lower({_folded('m.note')}) ILIKE %s OR
                    (CASE m.kind WHEN 'in' THEN 'nhap' ELSE 'xuat' END) ILIKE %s OR
                    to_char(m.movement_date, 'DD/MM/YYYY') ILIKE %s OR
                    to_char(m.movement_date, 'FMDD/FMMM/YYYY') ILIKE %s
                )"""
            )
            where_params.extend([pattern] * 9)

    where_sql = " AND ".join(where_filters)

    count_row = db._execute_query(
        f"""
        SELECT COUNT(*) AS total
        FROM public.inventory_movements m
        JOIN public.inventory_items i ON i.id = m.item_id
        WHERE {where_sql}
        """,
        tuple(where_params),
        fetch=True,
        commit=False,
    )
    total = int(count_row["total"]) if count_row and count_row.get("total") is not None else 0
    if total == 0:
        return [], 0

    limit = max(1, per_page)
    offset = max(0, (page - 1) * limit)

    query = f"""
        SELECT
            m.id,
            m.company_id,
            m.item_id,
            m.movement_date,
            m.kind,
            m.quantity,
            m.note,
            m.source,
            m.created_by,
            m.created_at,
            i.warehouse,
            i.code,
            i.name,
            CASE
                WHEN m.purchase_id IS NOT NULL AND COALESCE(p.pieces, 0) > 0 AND COALESCE(p.pieces_unit, '') <> ''
                THEN p.pieces_unit
                ELSE i.unit
            END AS unit,
            p.unit_price AS purchase_price
        FROM public.inventory_movements m
        JOIN public.inventory_items i ON i.id = m.item_id
        LEFT JOIN public.supplier_purchases p ON p.id = m.purchase_id
        WHERE {where_sql}
        ORDER BY m.movement_date DESC, m.id DESC
        LIMIT %s OFFSET %s
    """
    full_params = where_params + [limit, offset]
    rows = db._execute_query(query, tuple(full_params), fetchall=True, commit=False) or []

    for r in rows:
        r["quantity"] = Decimal(str(r["quantity"] or 0))
        r["wh_key"] = warehouse_key(r["warehouse"])
        r["unit"] = normalize_unit(r["unit"])

    return rows, total


def save_item(company_id: int, data: dict, item_id: int | None = None) -> dict:
    """Thêm hoặc cập nhật thông tin mặt hàng."""
    warehouse = str(data.get("warehouse") or "").strip()
    code = str(data.get("code") or "").strip()
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Vui lòng nhập tên hàng.")
    unit = normalize_unit(data.get("unit"))
    opening_qty = parse_quantity(data.get("opening_qty", 0))
    note = str(data.get("note") or "").strip()

    if code:
        check_sql = """
            SELECT id FROM public.inventory_items
            WHERE company_id = %s
              AND lower(warehouse) = lower(%s)
              AND lower(code) = lower(%s)
        """
        check_params: list[Any] = [company_id, warehouse, code]
        if item_id:
            check_sql += " AND id <> %s"
            check_params.append(item_id)
        existing = db._execute_query(check_sql, tuple(check_params), fetch=True, commit=False)
        if existing:
            raise ValueError("Mã hàng đã có trong kho này.")

    # The same item name twice in one warehouse splits its stock in two: refuse it ("tấm  4LY" = "Tấm 4ly").
    key = warehouse_key(warehouse)
    folded_name = " ".join(fold_text(name).split())
    same_name = db._execute_query(
        f"""SELECT id, warehouse, name FROM public.inventory_items
            WHERE company_id = %s AND TRIM(regexp_replace(lower({_folded('name')}), '\\s+', ' ', 'g')) = %s""",
        (company_id, folded_name), fetchall=True, commit=False) or []
    clash = next((r for r in same_name if warehouse_key(r["warehouse"]) == key and r["id"] != item_id), None)
    if not clash:
        alias_clashes = db._execute_query(
            f"""
            SELECT i.id, i.warehouse, i.name
            FROM public.inventory_item_aliases a
            JOIN public.inventory_items i ON i.id = a.item_id
            WHERE a.company_id = %s
              AND TRIM(regexp_replace(lower({_folded('a.name')}), '\\s+', ' ', 'g')) = %s
            ORDER BY i.id ASC
            """,
            (company_id, folded_name),
            fetchall=True,
            commit=False,
        ) or []
        clash = next((r for r in alias_clashes if warehouse_key(r["warehouse"]) == key and r["id"] != item_id), None)
    if clash:
        raise ValueError(f"Mặt hàng \"{clash['name']}\" đã có trong kho {clash['warehouse'] or 'này'}.")

    if item_id:
        existing_item = db._execute_query(
            "SELECT id FROM public.inventory_items WHERE id = %s AND company_id = %s",
            (item_id, company_id),
            fetch=True,
            commit=False,
        )
        if not existing_item:
            raise ValueError("Không tìm thấy mặt hàng.")
        sql = """
            UPDATE public.inventory_items
            SET warehouse = %s, code = %s, name = %s, unit = %s, opening_qty = %s, note = %s, updated_at = NOW()
            WHERE id = %s AND company_id = %s
            RETURNING id, company_id, warehouse, code, name, unit, opening_qty, note, created_at, updated_at
        """
        row = db._execute_query(sql, (warehouse, code, name, unit, opening_qty, note, item_id, company_id), fetch=True, commit=True)
        return row or {}
    else:
        sql = """
            INSERT INTO public.inventory_items (company_id, warehouse, code, name, unit, opening_qty, note, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id, company_id, warehouse, code, name, unit, opening_qty, note, created_at, updated_at
        """
        row = db._execute_query(sql, (company_id, warehouse, code, name, unit, opening_qty, note), fetch=True, commit=True)
        return row or {}


def delete_item(company_id: int, item_id: int) -> bool:
    """Xóa mặt hàng (các phiếu nhập/xuất liên quan sẽ tự động xóa do ON DELETE CASCADE)."""
    row = db._execute_query(
        "DELETE FROM public.inventory_items WHERE id = %s AND company_id = %s RETURNING id",
        (item_id, company_id),
        fetch=True,
        commit=True,
    )
    return bool(row)


def merge_items(company_id: int, source_id: int, target_id: int) -> dict:
    """Gộp mặt hàng nguồn vào mặt hàng đích (chuyển phiếu, cộng tồn đầu, giữ tên cũ làm tên phụ, xóa nguồn)."""
    try:
        source_id = int(source_id)
        target_id = int(target_id)
    except (ValueError, TypeError):
        raise ValueError("Mã mặt hàng không hợp lệ.")

    if source_id == target_id:
        raise ValueError("Hai mặt hàng phải khác nhau.")

    with db.transaction() as cur:
        cur.execute(
            """
            SELECT id, company_id, warehouse, code, name, unit, opening_qty, note
            FROM public.inventory_items
            WHERE id IN (%s, %s) AND company_id = %s
            FOR UPDATE
            """,
            (source_id, target_id, company_id),
        )
        rows = {r["id"]: r for r in (cur.fetchall() or [])}
        source = rows.get(source_id)
        target = rows.get(target_id)
        if not source or not target:
            raise ValueError("Không tìm thấy mặt hàng.")

        # 1. Chuyển phiếu nhập xuất từ source sang target
        cur.execute(
            """
            UPDATE public.inventory_movements
            SET item_id = %s
            WHERE company_id = %s AND item_id = %s
            """,
            (target_id, company_id, source_id),
        )
        moved_count = cur.rowcount

        # 2. Cộng tồn đầu và bổ sung thông tin thiếu cho target
        target_opening = Decimal(str(target.get("opening_qty") or 0))
        source_opening = Decimal(str(source.get("opening_qty") or 0))
        new_opening = target_opening + source_opening

        target_unit = str(target.get("unit") or "").strip()
        source_unit = str(source.get("unit") or "").strip()
        new_unit = target_unit if target_unit else source_unit

        target_note = str(target.get("note") or "").strip()
        source_note = str(source.get("note") or "").strip()
        new_note = target_note if target_note else source_note

        target_code = str(target.get("code") or "").strip()
        source_code = str(source.get("code") or "").strip()
        new_code = target_code if target_code else source_code

        cur.execute(
            """
            UPDATE public.inventory_items
            SET opening_qty = %s, unit = %s, note = %s, code = %s, updated_at = NOW()
            WHERE id = %s AND company_id = %s
            """,
            (new_opening, new_unit, new_note, new_code, target_id, company_id),
        )

        # 3. Chuyển các tên phụ của source sang target
        cur.execute(
            """
            UPDATE public.inventory_item_aliases
            SET item_id = %s
            WHERE company_id = %s AND item_id = %s
            """,
            (target_id, company_id, source_id),
        )

        # 4. Thêm tên của source làm tên phụ của target (nếu khác tên target sau khi gập dấu)
        source_name = str(source.get("name") or "").strip()
        target_name = str(target.get("name") or "").strip()
        folded_source = " ".join(fold_text(source_name).split())
        folded_target = " ".join(fold_text(target_name).split())

        if folded_source and folded_source != folded_target:
            cur.execute(
                """
                SELECT id, name
                FROM public.inventory_item_aliases
                WHERE company_id = %s AND item_id = %s
                """,
                (company_id, target_id),
            )
            existing_aliases = cur.fetchall() or []
            if not any(" ".join(fold_text(a["name"]).split()) == folded_source for a in existing_aliases):
                cur.execute(
                    """
                    INSERT INTO public.inventory_item_aliases (company_id, item_id, name, created_at)
                    VALUES (%s, %s, %s, NOW())
                    """,
                    (company_id, target_id, source_name),
                )

        # 5. Xóa source
        cur.execute(
            "DELETE FROM public.inventory_items WHERE id = %s AND company_id = %s",
            (source_id, company_id),
        )

    return {"target_id": target_id, "moved": moved_count}


def save_movement(
    company_id: int,
    data: dict,
    user_id: int | None = None,
    movement_id: int | None = None,
) -> dict:
    """Thêm hoặc sửa phiếu nhập / xuất kho."""
    raw_item_id = data.get("item_id")
    if not raw_item_id:
        raise ValueError("Vui lòng chọn mặt hàng.")
    try:
        item_id = int(raw_item_id)
    except (ValueError, TypeError):
        raise ValueError("Mặt hàng không hợp lệ.")

    item = db._execute_query(
        "SELECT id FROM public.inventory_items WHERE id = %s AND company_id = %s",
        (item_id, company_id),
        fetch=True,
        commit=False,
    )
    if not item:
        raise ValueError("Mặt hàng không thuộc công ty hiện tại.")

    kind = str(data.get("kind") or "").strip().lower()
    if kind not in ("in", "out"):
        raise ValueError("Loại phiếu không hợp lệ (phải là Nhập hoặc Xuất).")

    raw_mdate = data.get("movement_date")
    if not raw_mdate:
        movement_date = date.today()
    elif isinstance(raw_mdate, date):
        movement_date = raw_mdate
    else:
        mdate_str = str(raw_mdate).strip()
        try:
            from frontend.modules.transport.cash_bank_service import parse_flexible_date
            movement_date = parse_flexible_date(mdate_str) or date.today()
        except Exception:
            try:
                movement_date = datetime.strptime(mdate_str[:10], "%Y-%m-%d").date()
            except Exception:
                movement_date = date.today()

    raw_qty = data.get("quantity")
    if raw_qty is None or raw_qty == "":
        raise ValueError("Vui lòng nhập số lượng.")
    quantity = parse_quantity(raw_qty)

    note = str(data.get("note") or "").strip()
    source = str(data.get("source") or "manual").strip()

    if movement_id:
        existing = db._execute_query(
            "SELECT id FROM public.inventory_movements WHERE id = %s AND company_id = %s",
            (movement_id, company_id),
            fetch=True,
            commit=False,
        )
        if not existing:
            raise ValueError("Không tìm thấy phiếu.")
        sql = """
            UPDATE public.inventory_movements
            SET item_id = %s, movement_date = %s, kind = %s, quantity = %s, note = %s
            WHERE id = %s AND company_id = %s
            RETURNING id, company_id, item_id, movement_date, kind, quantity, note, source, created_by, created_at
        """
        row = db._execute_query(sql, (item_id, movement_date, kind, quantity, note, movement_id, company_id), fetch=True, commit=True)
        return row or {}
    else:
        sql = """
            INSERT INTO public.inventory_movements (company_id, item_id, movement_date, kind, quantity, note, source, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id, company_id, item_id, movement_date, kind, quantity, note, source, created_by, created_at
        """
        row = db._execute_query(sql, (company_id, item_id, movement_date, kind, quantity, note, source, user_id), fetch=True, commit=True)
        return row or {}


def delete_movement(company_id: int, movement_id: int) -> bool:
    """Xóa một phiếu nhập/xuất kho."""
    row = db._execute_query(
        "DELETE FROM public.inventory_movements WHERE id = %s AND company_id = %s RETURNING id",
        (movement_id, company_id),
        fetch=True,
        commit=True,
    )
    return bool(row)


def stock_name(product_name: str | None) -> str:
    """Lấy phần trước dấu '=' đầu tiên, bỏ khoảng trắng thừa."""
    s = str(product_name or "").strip()
    if "=" in s:
        s = s.split("=", 1)[0]
    return " ".join(s.split())


def find_inventory_item(company_id: int, warehouse: str, product_name: str) -> dict | None:
    """Tìm mặt hàng tồn kho theo kho + tên (không phân biệt dấu và chuẩn hóa khoảng trắng)."""
    wh = (warehouse or "").strip()
    pname = (product_name or "").strip()
    if not wh or not pname:
        return None
    normalized_pname = " ".join(fold_text(pname).split())
    find_sql = f"""
        SELECT id, warehouse, code, name, unit
        FROM public.inventory_items
        WHERE company_id = %s
          AND TRIM(regexp_replace(lower({_folded('name')}), '\\s+', ' ', 'g')) = %s
        ORDER BY id ASC
    """
    rows = db._execute_query(find_sql, (company_id, normalized_pname), fetchall=True, commit=False) or []
    key = warehouse_key(wh)
    item = next((row for row in rows if warehouse_key(row["warehouse"]) == key), None)
    if item:
        return item

    alias_sql = f"""
        SELECT i.id, i.warehouse, i.code, i.name, i.unit
        FROM public.inventory_item_aliases a
        JOIN public.inventory_items i ON i.id = a.item_id
        WHERE a.company_id = %s
          AND TRIM(regexp_replace(lower({_folded('a.name')}), '\\s+', ' ', 'g')) = %s
        ORDER BY i.id ASC
    """
    alias_rows = db._execute_query(alias_sql, (company_id, normalized_pname), fetchall=True, commit=False) or []
    return next((row for row in alias_rows if warehouse_key(row["warehouse"]) == key), None)


def sync_sale_movements(company_id: int, sale_id: int) -> None:
    """Tự động đồng bộ các phiếu xuất kho từ hóa đơn bán có chọn kho."""
    db._execute_query(
        "DELETE FROM public.inventory_movements WHERE company_id = %s AND sale_id = %s",
        (company_id, sale_id),
        commit=True,
    )

    sale = db._execute_query(
        """
        SELECT status, sale_date, invoice_number, source_invoice_number, customer_name
        FROM public.personal_sales
        WHERE company_id = %s AND id = %s
        """,
        (company_id, sale_id),
        fetch=True,
        commit=False,
    )
    if not sale:
        return

    status = (sale.get("status") or "delivered").strip().lower()
    if status not in ("delivered", "delivering"):
        return

    lines = db._execute_query(
        """
        SELECT product_name, unit, quantity, warehouse, stock_qty, stock_unit
        FROM public.personal_sale_items
        WHERE sale_id = %s
          AND warehouse IS NOT NULL AND TRIM(warehouse) <> ''
          AND (COALESCE(stock_qty, 0) > 0 OR COALESCE(quantity, 0) > 0)
        ORDER BY id ASC
        """,
        (sale_id,),
        fetchall=True,
        commit=False,
    ) or []

    if not lines:
        return

    inv_num = (sale.get("source_invoice_number") or sale.get("invoice_number") or "").strip()
    cust_name = (sale.get("customer_name") or "").strip()
    if inv_num:
        note = f"HĐ {inv_num} - {cust_name}" if cust_name else f"HĐ {inv_num}"
    else:
        note = f"Bán hàng - {cust_name}" if cust_name else "Bán hàng"

    sale_date = sale.get("sale_date") or date.today()

    for line in lines:
        wh = str(line.get("warehouse") or "").strip()
        pname = str(line.get("product_name") or "").strip()
        sname = stock_name(pname)
        if not sname or not wh:
            continue

        punit = normalize_unit(line.get("unit"))
        stock_unit = normalize_unit(line.get("stock_unit"))
        raw_sqty = line.get("stock_qty")
        stock_qty = parse_quantity(raw_sqty, Decimal("0")) if raw_sqty is not None and str(raw_sqty).strip() != "" else Decimal("0")
        line_qty = parse_quantity(line.get("quantity", 0))

        existing = find_inventory_item(company_id, wh, sname)

        if existing:
            item_id = existing["id"]
            existing_unit = str(existing.get("unit") or "").strip()
            if not existing_unit and stock_unit:
                db._execute_query(
                    """
                    UPDATE public.inventory_items
                    SET unit = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (stock_unit, item_id),
                    commit=True,
                )
                existing_unit = stock_unit
                existing["unit"] = stock_unit

            if stock_qty > 0:
                qty_out = stock_qty
            else:
                if fold_text(punit).strip().lower() != fold_text(existing_unit).strip().lower():
                    continue
                if line_qty <= 0:
                    continue
                qty_out = line_qty
        else:
            if stock_qty > 0:
                qty_out = stock_qty
                create_unit = stock_unit or punit
                new_item = db._execute_query(
                    """
                    INSERT INTO public.inventory_items (company_id, warehouse, code, name, unit, opening_qty, note, created_at, updated_at)
                    VALUES (%s, %s, '', %s, %s, 0, '', NOW(), NOW())
                    RETURNING id
                    """,
                    (company_id, wh, sname, create_unit),
                    fetch=True,
                    commit=True,
                )
                item_id = new_item["id"]
            else:
                continue

        db._execute_query(
            """
            INSERT INTO public.inventory_movements
                (company_id, item_id, movement_date, kind, quantity, note, source, sale_id, created_at)
            VALUES (%s, %s, %s, 'out', %s, %s, 'sale', %s, NOW())
            """,
            (company_id, item_id, sale_date, qty_out, note, sale_id),
            commit=True,
        )


def check_stock(
    company_id: int,
    warehouse: str,
    product_name: str,
    qty: Any = 0,
    exclude_sale_id: Any = None,
) -> dict:
    """Kiểm tra tồn kho hiện tại của mặt hàng."""
    wh = (warehouse or "").strip()
    pname = (product_name or "").strip()
    sname = stock_name(pname)
    if not wh or not sname:
        return {"found": False}

    item = find_inventory_item(company_id, wh, sname)
    if not item:
        return {"found": False}

    item_id = item["id"]
    item_row = db._execute_query(
        "SELECT opening_qty, unit FROM public.inventory_items WHERE id = %s AND company_id = %s",
        (item_id, company_id),
        fetch=True,
        commit=False,
    )
    opening = Decimal(str((item_row and item_row.get("opening_qty")) or 0))
    unit = normalize_unit((item_row and item_row.get("unit")) or item.get("unit"))

    clean_exclude_id = None
    if exclude_sale_id is not None and str(exclude_sale_id).strip() != "":
        try:
            clean_exclude_id = int(str(exclude_sale_id).strip())
        except (ValueError, TypeError):
            clean_exclude_id = None

    if clean_exclude_id is not None:
        mov_sql = """
            SELECT COALESCE(SUM(CASE WHEN kind = 'in' THEN quantity WHEN kind = 'out' THEN -quantity ELSE 0 END), 0) AS net
            FROM public.inventory_movements
            WHERE company_id = %s AND item_id = %s AND (sale_id IS NULL OR sale_id <> %s)
        """
        mov_params = (company_id, item_id, clean_exclude_id)
    else:
        mov_sql = """
            SELECT COALESCE(SUM(CASE WHEN kind = 'in' THEN quantity WHEN kind = 'out' THEN -quantity ELSE 0 END), 0) AS net
            FROM public.inventory_movements
            WHERE company_id = %s AND item_id = %s
        """
        mov_params = (company_id, item_id)

    mov_row = db._execute_query(mov_sql, mov_params, fetch=True, commit=False)
    net = Decimal(str((mov_row and mov_row.get("net")) or 0))

    closing = opening + net
    qty_val = parse_quantity(qty, Decimal("0"))
    after = closing - qty_val

    return {
        "found": True,
        "closing": float(closing),
        "unit": unit,
        "after": float(after),
    }


def sync_purchase_movements(company_id: int, purchase_id: int) -> None:
    """Tự động đồng bộ phiếu nhập kho từ dòng đơn nhập hàng có chọn kho."""
    db._execute_query(
        "DELETE FROM public.inventory_movements WHERE company_id = %s AND purchase_id = %s",
        (company_id, purchase_id),
        commit=True,
    )

    purchase = db._execute_query(
        """
        SELECT id, company_id, warehouse, product_name, pieces, pieces_unit, unit, quantity_kg, purchase_date, invoice_number, supplier_name
        FROM public.supplier_purchases
        WHERE company_id = %s AND id = %s
        """,
        (company_id, purchase_id),
        fetch=True,
        commit=False,
    )
    if not purchase:
        return

    wh = str(purchase.get("warehouse") or "").strip()
    pname = str(purchase.get("product_name") or "").strip()
    if not wh or not pname:
        return

    qty_kg = parse_quantity(purchase.get("quantity_kg", 0))
    raw_pieces = purchase.get("pieces")
    pieces = parse_quantity(raw_pieces, Decimal("0")) if raw_pieces is not None and str(raw_pieces).strip() != "" else Decimal("0")
    pieces_unit = normalize_unit(purchase.get("pieces_unit"))

    if qty_kg <= 0 and pieces <= 0:
        return

    # Stock comes in by the purchase book's "cây/tấm" count; the kg weight only when no count was written.
    quantity = pieces if pieces > 0 else qty_kg
    existing = find_inventory_item(company_id, wh, pname)
    if existing:
        item_id = existing["id"]
        current_unit = str(existing.get("unit") or "").strip()
        if pieces > 0 and fold_text(current_unit).strip() == "kg":
            # An item this sync once created in kg, with nothing else on it yet, now counts pieces.
            other = db._execute_query(
                "SELECT 1 FROM public.inventory_items i WHERE i.id = %s AND i.opening_qty = 0 "
                "AND NOT EXISTS (SELECT 1 FROM public.inventory_movements m WHERE m.item_id = i.id)",
                (item_id,), fetch=True, commit=False)
            if other:
                db._execute_query("UPDATE public.inventory_items SET unit = '', updated_at = NOW() WHERE id = %s",
                                  (item_id,), commit=True)
                current_unit = ""
        if pieces > 0 and pieces_unit and not current_unit:
            db._execute_query("UPDATE public.inventory_items SET unit = %s, updated_at = NOW() WHERE id = %s",
                              (pieces_unit, item_id), commit=True)
    else:
        new_unit = pieces_unit if (pieces > 0 and pieces_unit) else ("" if pieces > 0 else "Kg")

        new_item = db._execute_query(
            """
            INSERT INTO public.inventory_items (company_id, warehouse, code, name, unit, opening_qty, note, created_at, updated_at)
            VALUES (%s, %s, '', %s, %s, 0, '', NOW(), NOW())
            RETURNING id
            """,
            (company_id, wh, pname, new_unit),
            fetch=True,
            commit=True,
        )
        if not new_item:
            return
        item_id = new_item["id"]

    if quantity <= 0:
        return

    inv_num = str(purchase.get("invoice_number") or "").strip()
    supp_name = str(purchase.get("supplier_name") or "").strip()
    if inv_num:
        note = f"Nhập {inv_num} - {supp_name}" if supp_name else f"Nhập {inv_num}"
    else:
        note = f"Nhập hàng - {supp_name}" if supp_name else "Nhập hàng"

    raw_mdate = purchase.get("purchase_date")
    if not raw_mdate:
        movement_date = date.today()
    elif isinstance(raw_mdate, datetime):
        movement_date = raw_mdate.date()
    elif isinstance(raw_mdate, date):
        movement_date = raw_mdate
    else:
        try:
            from frontend.modules.transport.cash_bank_service import parse_flexible_date
            movement_date = parse_flexible_date(str(raw_mdate).strip()) or date.today()
        except Exception:
            movement_date = date.today()

    db._execute_query(
        """
        INSERT INTO public.inventory_movements
            (company_id, item_id, movement_date, kind, quantity, note, source, purchase_id, created_at)
        VALUES (%s, %s, %s, 'in', %s, %s, 'purchase', %s, NOW())
        """,
        (company_id, item_id, movement_date, quantity, note, purchase_id),
        commit=True,
    )


def import_excel(company_id: int, file_bytes: bytes, user_id: int | None = None) -> dict:
    """Nhập dữ liệu tồn kho từ file Excel (mọi sheet có tiêu đề hợp lệ)."""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    items_count = 0
    movements_count = 0
    today = date.today()

    for sheet in wb.worksheets:
        header_row_idx = None
        col_map: dict[str, int] = {}

        for row_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            row_text_folded = [fold_text(c).strip() for c in row if c is not None]
            has_code = any("ma hang" in c for c in row_text_folded)
            has_name = any("ten hang" in c for c in row_text_folded)
            # The header row names the item column plus a code, warehouse, unit or opening column (the template has no code).
            has_other = has_code or any(c.startswith("kho") or "dvt" in c or "ton dau" in c for c in row_text_folded)
            if has_name and has_other:
                header_row_idx = row_idx
                for col_idx, cell_val in enumerate(row):
                    if cell_val is None:
                        continue
                    f = fold_text(cell_val).strip()
                    if "ma hang" in f:
                        col_map["code"] = col_idx
                    elif "ten hang" in f:
                        col_map["name"] = col_idx
                    elif "ghi chu" in f:
                        col_map["note"] = col_idx
                    elif "kho hh" in f or f == "kho" or ("kho" in f and not any(k in f for k in ("nhap", "xuat", "ton"))):
                        col_map["warehouse"] = col_idx
                    elif "dvt" in f or "don vi" in f:
                        col_map["unit"] = col_idx
                    elif "ton dau" in f or "dau ky" in f:
                        col_map["opening"] = col_idx
                    elif "nhap" in f:
                        col_map["qty_in"] = col_idx
                    elif "xuat" in f:
                        col_map["qty_out"] = col_idx
                break

        if header_row_idx is None or "name" not in col_map:
            continue

        for row in sheet.iter_rows(min_row=header_row_idx + 1, values_only=True):
            if not row:
                continue
            name_idx = col_map.get("name")
            name_val = row[name_idx] if name_idx is not None and name_idx < len(row) else None
            if name_val is None:
                continue
            name = str(name_val).strip()
            if not name:
                continue

            wh_idx = col_map.get("warehouse")
            warehouse = str(row[wh_idx]).strip() if wh_idx is not None and wh_idx < len(row) and row[wh_idx] is not None else ""

            code_idx = col_map.get("code")
            code = str(row[code_idx]).strip() if code_idx is not None and code_idx < len(row) and row[code_idx] is not None else ""

            unit_idx = col_map.get("unit")
            unit = normalize_unit(row[unit_idx]) if unit_idx is not None and unit_idx < len(row) and row[unit_idx] is not None else ""
            note_idx = col_map.get("note")
            note = str(row[note_idx]).strip() if note_idx is not None and note_idx < len(row) and row[note_idx] is not None else ""

            op_idx = col_map.get("opening")
            opening_val = row[op_idx] if op_idx is not None and op_idx < len(row) else 0

            in_idx = col_map.get("qty_in")
            qty_in_val = row[in_idx] if in_idx is not None and in_idx < len(row) else 0

            out_idx = col_map.get("qty_out")
            qty_out_val = row[out_idx] if out_idx is not None and out_idx < len(row) else 0

            opening = parse_quantity(opening_val, Decimal("0"))
            qty_in = parse_quantity(qty_in_val, Decimal("0"))
            qty_out = parse_quantity(qty_out_val, Decimal("0"))

            # Upsert theo (company, kho, mã)
            if code:
                existing = db._execute_query(
                    """
                    SELECT id FROM public.inventory_items
                    WHERE company_id = %s AND lower(warehouse) = lower(%s) AND lower(code) = lower(%s)
                    """,
                    (company_id, warehouse, code),
                    fetch=True,
                    commit=False,
                )
            else:
                existing = db._execute_query(
                    """
                    SELECT id FROM public.inventory_items
                    WHERE company_id = %s AND lower(warehouse) = lower(%s) AND (code IS NULL OR code = '') AND lower(name) = lower(%s)
                    """,
                    (company_id, warehouse, name),
                    fetch=True,
                    commit=False,
                )

            if existing:
                item_id = existing["id"]
                db._execute_query(
                    """
                    UPDATE public.inventory_items
                    SET name = %s, unit = %s, opening_qty = %s, note = COALESCE(NULLIF(%s, ''), note), updated_at = NOW()
                    WHERE id = %s
                    """,
                    (name, unit, opening, note, item_id),
                    commit=True,
                )
            else:
                item_row = db._execute_query(
                    """
                    INSERT INTO public.inventory_items (company_id, warehouse, code, name, unit, opening_qty, note, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING id
                    """,
                    (company_id, warehouse, code, name, unit, opening, note),
                    fetch=True,
                    commit=True,
                )
                item_id = item_row["id"]
            items_count += 1

            # The sheet is the whole truth for this item: its earlier Excel figures are replaced, not added to.
            db._execute_query(
                "DELETE FROM public.inventory_movements WHERE company_id = %s AND item_id = %s AND source = 'excel'",
                (company_id, item_id),
                commit=True,
            )
            if qty_in > 0 or qty_out > 0:
                if qty_in > 0:
                    db._execute_query(
                        """
                        INSERT INTO public.inventory_movements
                            (company_id, item_id, movement_date, kind, quantity, note, source, created_by, created_at)
                        VALUES (%s, %s, %s, 'in', %s, 'Số liệu sổ Excel', 'excel', %s, NOW())
                        """,
                        (company_id, item_id, today, qty_in, user_id),
                        commit=True,
                    )
                    movements_count += 1
                if qty_out > 0:
                    db._execute_query(
                        """
                        INSERT INTO public.inventory_movements
                            (company_id, item_id, movement_date, kind, quantity, note, source, created_by, created_at)
                        VALUES (%s, %s, %s, 'out', %s, 'Số liệu sổ Excel', 'excel', %s, NOW())
                        """,
                        (company_id, item_id, today, qty_out, user_id),
                        commit=True,
                    )
                    movements_count += 1

    return {"items": items_count, "movements": movements_count}


def import_template_bytes() -> bytes:
    """Blank Excel the import reads: header row, Kho and ĐVT drop-downs (other values may still be typed)."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tồn kho"
    headers = ["STT", "Kho HH", "Tên hàng", "ĐVT", "Tồn đầu", "Nhập kho", "Xuất kho", "Ghi chú"]
    widths = [6, 10, 38, 9, 11, 11, 11, 28]
    ws.append(headers)
    for col, width in enumerate(widths, start=1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E79")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[cell.column_letter].width = width
    ws.freeze_panes = "A2"
    for formula, cells in (('"ĐV,TL"', "B2:B2000"), ('"Cây,Tấm,Bó,Bộ,Cuộn,Kg"', "D2:D2000")):
        rule = DataValidation(type="list", formula1=formula, allow_blank=True, showErrorMessage=False)
        ws.add_data_validation(rule)
        rule.add(cells)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
