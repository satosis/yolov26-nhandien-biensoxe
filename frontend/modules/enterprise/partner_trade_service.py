"""Trade partner ledger service (Sổ mua/bán hàng với đối tác)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
import logging
import secrets
from typing import Any, Optional

from backend.services.database import db

logger = logging.getLogger(__name__)


def list_partners(company_id: int) -> list[dict]:
    """List trade partners for a company, ordered by name."""
    if not company_id:
        return []
    rows = db._execute_query(
        """
        SELECT p.id, p.company_id, p.name, p.opening_balance, p.title, p.owe_label, p.is_default,
               p.created_at, p.updated_at,
               (SELECT COUNT(*) FROM public.trade_partner_entries e WHERE e.partner_id = p.id) AS entry_count
        FROM public.trade_partners p
        WHERE p.company_id = %s
        ORDER BY p.name ASC
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    )
    return rows or []


def default_partner(partners: list[dict]) -> Optional[dict]:
    """Sổ mở sẵn khi vào trang: sổ được đánh dấu mặc định, không có thì sổ nhiều dòng nhất."""
    if not partners:
        return None
    for partner in partners:
        if partner.get("is_default"):
            return partner
    return max(partners, key=lambda p: int(p.get("entry_count") or 0))


def get_partner(company_id: int, partner_id: int) -> Optional[dict]:
    """Retrieve a single trade partner by id and company."""
    if not company_id or not partner_id:
        return None
    row = db._execute_query(
        """
        SELECT id, company_id, name, opening_balance, title, owe_label, is_default, created_at, updated_at
        FROM public.trade_partners
        WHERE company_id = %s AND id = %s
        """,
        (company_id, partner_id),
        fetch=True,
        commit=False,
    )
    return row if row else None


def get_share_token(company_id: int, partner_id: int, renew: bool = False) -> str:
    """Get or generate a share token for a trade partner."""
    if not company_id or not partner_id:
        raise ValueError("Không tìm thấy đối tác.")
    row = db._execute_query(
        """
        SELECT id, share_token
        FROM public.trade_partners
        WHERE company_id = %s AND id = %s
        """,
        (company_id, partner_id),
        fetch=True,
        commit=False,
    )
    if not row:
        raise ValueError("Không tìm thấy đối tác.")

    token = row.get("share_token")
    if renew or not token:
        token = secrets.token_urlsafe(24)
        db._execute_query(
            """
            UPDATE public.trade_partners
            SET share_token = %s, updated_at = NOW()
            WHERE id = %s AND company_id = %s
            """,
            (token, partner_id, company_id),
            commit=True,
        )
    return str(token)


def get_partner_by_share_token(token: str) -> Optional[dict]:
    """Retrieve a trade partner by its share token (public access)."""
    clean_token = (token or "").strip()
    if not clean_token:
        return None
    row = db._execute_query(
        """
        SELECT id, company_id, name, opening_balance, title, owe_label
        FROM public.trade_partners
        WHERE share_token = %s
        """,
        (clean_token,),
        fetch=True,
        commit=False,
    )
    return row if row else None


def save_partner(
    company_id: int,
    name: str,
    opening_balance: Any = 0,
    partner_id: Optional[int] = None,
    title: str = "",
    owe_label: str = "",
    is_default: bool = False,
) -> dict:
    """Create or update a trade partner. Raises ValueError if name duplicates in company."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Vui lòng nhập tên đối tác.")

    clean_title = (title or "").strip()
    clean_owe_label = (owe_label or "").strip()

    if opening_balance is None or opening_balance == "":
        bal = Decimal(0)
    else:
        bal = Decimal(str(opening_balance))

    if partner_id:
        dup = db._execute_query(
            """
            SELECT id FROM public.trade_partners
            WHERE company_id = %s AND LOWER(TRIM(name)) = LOWER(TRIM(%s)) AND id != %s
            LIMIT 1
            """,
            (company_id, clean_name, partner_id),
            fetch=True,
            commit=False,
        )
    else:
        dup = db._execute_query(
            """
            SELECT id FROM public.trade_partners
            WHERE company_id = %s AND LOWER(TRIM(name)) = LOWER(TRIM(%s))
            LIMIT 1
            """,
            (company_id, clean_name),
            fetch=True,
            commit=False,
        )

    if dup:
        raise ValueError("Đối tác đã có.")

    if partner_id:
        row = db._execute_query(
            """
            UPDATE public.trade_partners
            SET name = %s, opening_balance = %s, title = %s, owe_label = %s, is_default = %s, updated_at = NOW()
            WHERE id = %s AND company_id = %s
            RETURNING id, company_id, name, opening_balance, title, owe_label, is_default, created_at, updated_at
            """,
            (clean_name, bal, clean_title, clean_owe_label, bool(is_default), partner_id, company_id),
            fetch=True,
            commit=True,
        )
        if not row:
            raise ValueError("Không tìm thấy đối tác để cập nhật.")
    else:
        row = db._execute_query(
            """
            INSERT INTO public.trade_partners (company_id, name, opening_balance, title, owe_label, is_default, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id, company_id, name, opening_balance, title, owe_label, is_default, created_at, updated_at
            """,
            (company_id, clean_name, bal, clean_title, clean_owe_label, bool(is_default)),
            fetch=True,
            commit=True,
        ) or {}
    # Chỉ một sổ mặc định trong một công ty
    if is_default and row.get("id"):
        db._execute_query(
            "UPDATE public.trade_partners SET is_default = FALSE WHERE company_id = %s AND id <> %s AND is_default",
            (company_id, row["id"]),
            commit=True,
        )
    return row


def delete_partner(company_id: int, partner_id: int) -> bool:
    """Delete a trade partner (cascade deletes entries in DB)."""
    if not company_id or not partner_id:
        return False
    return bool(db._execute_query(
        "DELETE FROM public.trade_partners WHERE id = %s AND company_id = %s",
        (partner_id, company_id),
        commit=True,
    ))


def count_entries(company_id: int, partner_id: int, q: str = "") -> int:
    """Count total entries matching search filter for a partner."""
    if not company_id or not partner_id:
        return 0
    q_clean = (q or "").strip()
    q_like = f"%{q_clean}%"
    row = db._execute_query(
        """
        SELECT COUNT(*) AS total
        FROM public.trade_partner_entries
        WHERE company_id = %s AND partner_id = %s
          AND (%s = '' OR item_name ILIKE %s OR buyer ILIKE %s OR note ILIKE %s OR to_char(entry_date, 'DD/MM/YYYY') LIKE %s)
        """,
        (company_id, partner_id, q_clean, q_like, q_like, q_like, q_like),
        fetch=True,
        commit=False,
    ) or {}
    return int(row.get("total") or 0)


def ledger(
    company_id: int,
    partner_id: int,
    q: str = "",
    page: int = 1,
    per_page: int = 100,
) -> tuple[list[dict], int, dict]:
    """
    Get partner trade ledger entries with cumulative balance calculated across ALL partner entries.
    Returns (rows, total, totals).
    """
    empty_totals = {"take": Decimal(0), "owe": Decimal(0), "balance": Decimal(0), "opening": Decimal(0)}
    if not company_id or not partner_id:
        return [], 0, empty_totals

    partner = get_partner(company_id, partner_id)
    opening = Decimal(partner["opening_balance"] or 0) if partner else Decimal(0)

    # Calculate overall totals for this partner (independent of q)
    sum_row = db._execute_query(
        """
        SELECT
            COALESCE(SUM(CASE WHEN side = 'take' THEN amount ELSE 0 END), 0) AS take,
            COALESCE(SUM(CASE WHEN side = 'owe' THEN amount ELSE 0 END), 0) AS owe
        FROM public.trade_partner_entries
        WHERE company_id = %s AND partner_id = %s
        """,
        (company_id, partner_id),
        fetch=True,
        commit=False,
    ) or {}
    take_total = Decimal(sum_row.get("take") or 0)
    owe_total = Decimal(sum_row.get("owe") or 0)
    balance_total = opening + take_total - owe_total
    totals = {
        "take": take_total,
        "owe": owe_total,
        "balance": balance_total,
        "opening": opening,
    }

    q_clean = (q or "").strip()
    q_like = f"%{q_clean}%"
    total = count_entries(company_id, partner_id, q_clean)

    p_num = max(1, int(page or 1))
    offset = (p_num - 1) * per_page

    query = """
    WITH ledger AS (
      SELECT e.*,
             EXISTS (SELECT 1 FROM public.trade_entry_details d WHERE d.entry_id = e.id) AS has_details,
             p.opening_balance + SUM(CASE WHEN e.side = 'owe' THEN -e.amount ELSE e.amount END)
                 OVER (ORDER BY e.entry_date, e.id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS balance
      FROM public.trade_partner_entries e
      JOIN public.trade_partners p ON p.id = e.partner_id
      WHERE e.company_id = %s AND e.partner_id = %s
    )
    SELECT * FROM ledger
    WHERE (%s = '' OR item_name ILIKE %s OR buyer ILIKE %s OR note ILIKE %s OR to_char(entry_date, 'DD/MM/YYYY') LIKE %s)
    ORDER BY entry_date, id
    LIMIT %s OFFSET %s
    """
    rows = db._execute_query(
        query,
        (company_id, partner_id, q_clean, q_like, q_like, q_like, q_like, per_page, offset),
        fetchall=True,
        commit=False,
    ) or []

    return rows, total, totals


def save_entry(
    company_id: int,
    partner_id: int,
    data: dict,
    user_id: Optional[int] = None,
    entry_id: Optional[int] = None,
) -> dict:
    """Create or update a ledger entry."""
    entry_date = data.get("entry_date")
    if not entry_date:
        raise ValueError("Ngày không hợp lệ.")
    if isinstance(entry_date, str):
        try:
            entry_date = date.fromisoformat(entry_date.strip())
        except Exception:
            try:
                entry_date = datetime.strptime(entry_date.strip(), "%d/%m/%Y").date()
            except Exception:
                raise ValueError("Ngày không hợp lệ.")

    item_name = str(data.get("item_name") or "").strip()
    buyer = str(data.get("buyer") or "").strip()
    quantity = data.get("quantity")
    if quantity is not None and not isinstance(quantity, Decimal):
        try:
            quantity = Decimal(str(quantity))
        except Exception:
            raise ValueError("Số lượng không hợp lệ.")

    unit_price = data.get("unit_price")
    if unit_price is not None and not isinstance(unit_price, Decimal):
        try:
            unit_price = Decimal(str(unit_price))
        except Exception:
            raise ValueError("Đơn giá không hợp lệ.")

    amount = data.get("amount")
    if amount is not None and not isinstance(amount, Decimal):
        try:
            amount = Decimal(str(amount))
        except Exception:
            raise ValueError("Số tiền không hợp lệ.")

    # Nếu amount None mà có cả quantity và unit_price → amount = round(quantity × unit_price, 0)
    if amount is None and quantity is not None and unit_price is not None:
        amount = (quantity * unit_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    if amount is None:
        amount = Decimal(0)

    # Dòng chỉ có số tiền (không Số lượng, không Đơn giá) không mang ký hiệu tự điền của sổ.
    if quantity is None and unit_price is None and buyer:
        auto_marks = {"v", "m", *ledger_marks(company_id, partner_id).values()}
        if buyer.lower() in auto_marks:
            buyer = ""

    # side khác 'owe' coi là 'take'
    side = "owe" if str(data.get("side") or "").strip().lower() == "owe" else "take"
    qty_formula = str(data.get("qty_formula") or "").strip()
    note = str(data.get("note") or "").strip()

    if entry_id:
        existing_details = list_details(company_id, entry_id)
        if existing_details:
            amount = detail_totals(existing_details)["total"]

        row = db._execute_query(
            """
            UPDATE public.trade_partner_entries
            SET entry_date = %s, item_name = %s, buyer = %s, quantity = %s, unit_price = %s,
                amount = %s, side = %s, qty_formula = %s, note = %s, updated_at = NOW()
            WHERE id = %s AND company_id = %s AND partner_id = %s
            RETURNING id, company_id, partner_id, entry_date, item_name, buyer, quantity, unit_price, amount, side
            """,
            (entry_date, item_name, buyer, quantity, unit_price, amount, side, qty_formula, note, entry_id, company_id, partner_id),
            fetch=True,
            commit=True,
        )
        if not row:
            raise ValueError("Không tìm thấy dòng sổ để cập nhật.")
        return row
    else:
        row = db._execute_query(
            """
            INSERT INTO public.trade_partner_entries
              (company_id, partner_id, entry_date, item_name, buyer, quantity, unit_price, amount, side, qty_formula, note, created_by, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id, company_id, partner_id, entry_date, item_name, buyer, quantity, unit_price, amount, side
            """,
            (company_id, partner_id, entry_date, item_name, buyer, quantity, unit_price, amount, side, qty_formula, note, user_id),
            fetch=True,
            commit=True,
        )
        return row or {}


def delete_entry(company_id: int, entry_id: int) -> bool:
    """Delete a ledger entry by id and company."""
    if not company_id or not entry_id:
        return False
    return bool(db._execute_query(
        "DELETE FROM public.trade_partner_entries WHERE id = %s AND company_id = %s",
        (entry_id, company_id),
        commit=True,
    ))


def get_entry(company_id: int, entry_id: int) -> Optional[dict]:
    """Retrieve a single ledger entry by id and company."""
    if not company_id or not entry_id:
        return None
    row = db._execute_query(
        """
        SELECT *
        FROM public.trade_partner_entries
        WHERE id = %s AND company_id = %s
        """,
        (entry_id, company_id),
        fetch=True,
        commit=False,
    )
    return row if row else None


def list_details(company_id: int, entry_id: int) -> list[dict]:
    """List detail lines for an entry, ordered by position and id."""
    if not company_id or not entry_id:
        return []
    rows = db._execute_query(
        """
        SELECT id, company_id, entry_id, position, kind, item_name, quantity, qty_formula, unit_price, amount, created_at
        FROM public.trade_entry_details
        WHERE company_id = %s AND entry_id = %s
        ORDER BY position ASC, id ASC
        """,
        (company_id, entry_id),
        fetchall=True,
        commit=False,
    )
    return rows or []


def detail_totals(lines: list[dict]) -> dict:
    """Calculate summary totals for entry detail lines."""
    items_qty = Decimal(0)
    items = Decimal(0)
    plus = Decimal(0)
    minus = Decimal(0)

    for line in lines or []:
        kind = str(line.get("kind") or "item").strip().lower()
        amt = Decimal(str(line.get("amount") or 0))
        if kind == "item":
            items += amt
            qty = line.get("quantity")
            if qty is not None:
                items_qty += Decimal(str(qty))
        elif kind == "plus":
            plus += amt
        elif kind == "minus":
            minus += amt

    subtotal = items + plus
    total = items + plus - minus
    return {
        "items_qty": items_qty,
        "items": items,
        "plus": plus,
        "minus": minus,
        "subtotal": subtotal,
        "total": total,
    }


def save_details(company_id: int, entry_id: int, lines: list[dict]) -> dict:
    """Save detail lines for an entry, replace old lines, and update entry amount if lines exist."""
    if not company_id or not entry_id:
        raise ValueError("Thông tin không hợp lệ.")

    clean_lines = []
    for line in lines or []:
        kind = str(line.get("kind") or "item").strip().lower()
        if kind not in ("item", "plus", "minus"):
            kind = "item"
        name = str(line.get("item_name") or "").strip()
        qty = line.get("quantity")
        if qty is not None and not isinstance(qty, Decimal):
            qty = Decimal(str(qty))
        price = line.get("unit_price")
        if price is not None and not isinstance(price, Decimal):
            price = Decimal(str(price))
        amt = line.get("amount")
        if amt is not None and not isinstance(amt, Decimal):
            amt = Decimal(str(amt))
        qty_formula = str(line.get("qty_formula") or "").strip()

        # Bỏ dòng hoàn toàn trống (không tên, không số)
        if not name and qty is None and price is None and (amt is None or amt == 0):
            continue

        if amt is None and qty is not None and price is not None:
            amt = (qty * price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if amt is None:
            amt = Decimal(0)

        clean_lines.append({
            "kind": kind,
            "item_name": name,
            "quantity": qty,
            "qty_formula": qty_formula,
            "unit_price": price,
            "amount": amt,
        })

    totals = detail_totals(clean_lines)

    with db.transaction() as cursor:
        cursor.execute(
            "DELETE FROM public.trade_entry_details WHERE entry_id = %s AND company_id = %s",
            (entry_id, company_id),
        )
        for pos, line in enumerate(clean_lines):
            cursor.execute(
                """
                INSERT INTO public.trade_entry_details
                    (company_id, entry_id, position, kind, item_name, quantity, qty_formula, unit_price, amount, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    company_id,
                    entry_id,
                    pos,
                    line["kind"],
                    line["item_name"],
                    line["quantity"],
                    line["qty_formula"],
                    line["unit_price"],
                    line["amount"],
                ),
            )
        if clean_lines:
            cursor.execute(
                """
                UPDATE public.trade_partner_entries
                SET amount = %s, updated_at = NOW()
                WHERE id = %s AND company_id = %s
                """,
                (totals["total"], entry_id, company_id),
            )

    return totals


DEFAULT_MARKS = {"take": "v", "owe": "m"}


def ledger_marks(company_id: int, partner_id: int) -> dict:
    """Ký hiệu tự điền cho từng cột của một sổ: ký hiệu ngắn (1-2 chữ) dùng nhiều nhất ở cột đó trong chính sổ này,
    chưa có thì Thành tiền = "v", cột nợ = "m". Ví dụ sổ C Vinh: v / m; sổ Phương Hải: m / p."""
    marks = dict(DEFAULT_MARKS)
    if not company_id or not partner_id:
        return marks
    rows = db._execute_query(
        """
        SELECT side, LOWER(TRIM(buyer)) AS mark, COUNT(*) AS uses, MAX(id) AS last_id
        FROM public.trade_partner_entries
        WHERE company_id = %s AND partner_id = %s AND LENGTH(TRIM(buyer)) BETWEEN 1 AND 2
        GROUP BY side, LOWER(TRIM(buyer))
        ORDER BY uses DESC, last_id DESC
        """,
        (company_id, partner_id),
        fetchall=True,
        commit=False,
    ) or []
    seen = set()
    for row in rows:
        side = row.get("side")
        if side in marks and side not in seen:
            marks[side] = row["mark"]
            seen.add(side)
    return marks


def list_buyers(company_id: int) -> list[str]:
    """List distinct non-empty buyer names for autocomplete."""
    if not company_id:
        return []
    rows = db._execute_query(
        """
        SELECT DISTINCT buyer
        FROM public.trade_partner_entries
        WHERE company_id = %s AND buyer IS NOT NULL AND TRIM(buyer) != ''
        ORDER BY buyer ASC
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []
    return [r["buyer"] for r in rows if r.get("buyer")]
