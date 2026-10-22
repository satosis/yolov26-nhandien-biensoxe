"""Company debt ledgers scoped to an owning (seller) company."""

from datetime import date, datetime
from decimal import Decimal
import logging
from typing import Optional

from psycopg2.extras import RealDictCursor

from backend.services.database import db

logger = logging.getLogger(__name__)


def get_company_debt(company_id: int, owner_company_id: int | None) -> Decimal:
    """Return a partner balance only inside the caller's company ledger."""
    if not owner_company_id:
        return Decimal("0")
    row = db._execute_query(
        """SELECT current_debt FROM public.company_debt_accounts
           WHERE company_id = %s AND owner_company_id = %s""",
        (company_id, owner_company_id), fetch=True, commit=False,
    )
    return Decimal(str((row or {}).get("current_debt") or "0"))


def adjust_company_debt(
    company_id: int,
    amount: Decimal | float | int | str,
    owner_company_id: int | None,
    debt_type: str = "manual_add",
    note: str = "",
    reference_doc: str = "",
    recorded_date: Optional[date | str] = None,
    user_id: Optional[int] = None,
) -> tuple[bool, str, Decimal]:
    """Adjust one partner's balance in the specified owning company's ledger."""
    if not owner_company_id:
        return False, "Chưa chọn doanh nghiệp.", Decimal("0")
    try:
        val = Decimal(str(amount))
    except Exception:
        return False, "Số tiền không hợp lệ.", Decimal("0")
    if val == 0:
        return False, "Số tiền phải khác 0.", get_company_debt(company_id, owner_company_id)

    if recorded_date is None:
        rec_date = date.today()
    elif isinstance(recorded_date, str):
        try:
            rec_date = datetime.strptime(recorded_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            rec_date = date.today()
    else:
        rec_date = recorded_date

    try:
        with db._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """INSERT INTO public.company_debt_accounts
                       (owner_company_id, company_id, current_debt)
                       VALUES (%s, %s, 0)
                       ON CONFLICT (owner_company_id, company_id) DO NOTHING""",
                    (owner_company_id, company_id),
                )
                cursor.execute(
                    """UPDATE public.company_debt_accounts
                       SET current_debt = current_debt + %s, updated_at = NOW()
                       WHERE owner_company_id = %s AND company_id = %s
                       RETURNING current_debt""",
                    (val, owner_company_id, company_id),
                )
                updated = cursor.fetchone()
                if not updated:
                    conn.rollback()
                    return False, "Không tìm thấy đơn vị.", Decimal("0")
                cursor.execute(
                    """INSERT INTO public.company_debt_logs
                       (owner_company_id, company_id, amount, debt_type, note,
                        reference_doc, recorded_date, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (owner_company_id, company_id, val, debt_type, note.strip(),
                     (reference_doc or "").strip(), rec_date, user_id),
                )
            conn.commit()
        new_balance = Decimal(str(updated.get("current_debt") or "0"))
        action_desc = "tăng nợ (+)" if val > 0 else "giảm nợ / thanh toán (-)"
        return True, f"Đã ghi nhận {action_desc} thành công.", new_balance
    except Exception as exc:
        logger.exception("Error adjusting company debt: %s", exc)
        return False, f"Lỗi cập nhật công nợ: {exc}", Decimal("0")


def get_company_debt_logs(
    company_id: int,
    owner_company_id: int | None,
    limit: int = 100,
) -> list[dict]:
    """Get history for a partner, restricted to its owning company."""
    if not owner_company_id:
        return []
    rows = db._execute_query(
        """SELECT l.id, l.company_id, l.owner_company_id, l.amount, l.debt_type,
                  l.note, l.recorded_date, l.reference_doc, l.created_at, l.created_by,
                  COALESCE(u.full_name, u.username) AS creator_name
           FROM public.company_debt_logs l
           LEFT JOIN public.users u ON u.id = l.created_by
           WHERE l.company_id = %s AND l.owner_company_id = %s
           ORDER BY l.recorded_date DESC, l.id DESC
           LIMIT %s""",
        (company_id, owner_company_id, limit), fetchall=True, commit=False,
    ) or []

    type_labels = {
        "initial": "Số dư ban đầu",
        "manual_add": "Ghi nợ ngoài",
        "payment": "Thanh toán nợ",
        "trip_debt": "Nợ chuyến hàng",
        "invoice_debt": "Nợ hóa đơn",
    }
    for row in rows:
        value = Decimal(str(row["amount"]))
        row["amount"] = float(value)
        row["amount_fmt"] = "{:,.0f} đ".format(abs(value)).replace(",", ".")
        row["is_increase"] = value > 0
        recorded = row.get("recorded_date")
        row["recorded_date_str"] = recorded.strftime("%d/%m/%Y") if hasattr(recorded, "strftime") else str(recorded or "")
        row["recorded_date"] = recorded.isoformat() if hasattr(recorded, "isoformat") else str(recorded or "")
        if row.get("created_at") and hasattr(row["created_at"], "isoformat"):
            row["created_at"] = row["created_at"].isoformat()
        row["type_label"] = type_labels.get(row["debt_type"], row["debt_type"])
    return rows


def delete_company_debt_log(
    log_id: int,
    owner_company_id: int | None,
) -> tuple[bool, str, Decimal]:
    """Delete a ledger entry only when it belongs to the requested owner."""
    if not owner_company_id:
        return False, "Chưa chọn doanh nghiệp.", Decimal("0")
    try:
        with db._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """SELECT company_id, amount FROM public.company_debt_logs
                       WHERE id = %s AND owner_company_id = %s FOR UPDATE""",
                    (log_id, owner_company_id),
                )
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return False, "Không tìm thấy bản ghi công nợ.", Decimal("0")
                company_id = row["company_id"]
                amount = Decimal(str(row["amount"]))
                cursor.execute(
                    "DELETE FROM public.company_debt_logs WHERE id = %s AND owner_company_id = %s",
                    (log_id, owner_company_id),
                )
                cursor.execute(
                    """UPDATE public.company_debt_accounts
                       SET current_debt = current_debt - %s, updated_at = NOW()
                       WHERE company_id = %s AND owner_company_id = %s
                       RETURNING current_debt""",
                    (amount, company_id, owner_company_id),
                )
                updated = cursor.fetchone()
            conn.commit()
        new_balance = Decimal(str(updated["current_debt"] or "0")) if updated else Decimal("0")
        return True, "Đã xóa bản ghi công nợ.", new_balance
    except Exception as exc:
        logger.exception("Error deleting company debt log: %s", exc)
        return False, f"Lỗi khi xóa: {exc}", Decimal("0")


def update_company_debt_log(
    log_id: int,
    owner_company_id: int | None,
    amount: Decimal,
    note: str,
    reference_doc: str,
    recorded_date: date,
) -> tuple[bool, str, Decimal]:
    """Edit a manual ledger movement and apply its delta to that owner's balance."""
    if not owner_company_id:
        return False, "Chưa chọn doanh nghiệp.", Decimal("0")
    if not amount.is_finite() or amount <= 0:
        return False, "Số tiền phải lớn hơn 0.", Decimal("0")
    try:
        with db._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """SELECT company_id, amount, debt_type
                         FROM public.company_debt_logs
                        WHERE id = %s AND owner_company_id = %s
                          AND debt_type IN ('manual_add', 'payment')
                        FOR UPDATE""",
                    (log_id, owner_company_id),
                )
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return False, "Không tìm thấy giao dịch có thể sửa.", Decimal("0")
                company_id = row["company_id"]
                old_amount = Decimal(str(row["amount"]))
                new_amount = amount if old_amount > 0 else -amount
                cursor.execute(
                    """UPDATE public.company_debt_logs
                          SET amount = %s, note = %s, reference_doc = %s, recorded_date = %s
                        WHERE id = %s AND owner_company_id = %s""",
                    (new_amount, note.strip(), reference_doc.strip(), recorded_date,
                     log_id, owner_company_id),
                )
                cursor.execute(
                    """UPDATE public.company_debt_accounts
                          SET current_debt = current_debt + %s, updated_at = NOW()
                        WHERE company_id = %s AND owner_company_id = %s
                        RETURNING current_debt""",
                    (new_amount - old_amount, company_id, owner_company_id),
                )
                account = cursor.fetchone()
                if not account:
                    conn.rollback()
                    return False, "Không tìm thấy sổ công nợ của doanh nghiệp.", Decimal("0")
            conn.commit()
        return True, "Đã cập nhật giao dịch công nợ.", Decimal(str(account["current_debt"] or 0))
    except Exception as exc:
        logger.exception("Error updating company debt log: %s", exc)
        return False, f"Lỗi khi cập nhật: {exc}", Decimal("0")
