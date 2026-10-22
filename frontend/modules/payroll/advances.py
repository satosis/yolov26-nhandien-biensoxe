"""Persist salary advances and their recipient's transfer QR atomically."""

from datetime import date
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import quote, urlencode
from uuid import UUID

from psycopg2.extras import RealDictCursor

from backend.services.database import db
from frontend.modules.bank_transfer import bank_code, remove_accents
from frontend.modules.payroll.staff_payroll import calculate_payroll_row


def _validate_amount(amount: Decimal) -> Decimal:
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Số tiền tạm ứng không hợp lệ.") from exc
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999999"):
        raise ValueError("Số tiền tạm ứng phải lớn hơn 0 và không vượt quá 999.999.999.999 đồng.")
    if amount != amount.to_integral_value():
        raise ValueError("Số tiền tạm ứng phải là số nguyên đồng.")
    return amount


def _request_uuid(request_id: str) -> UUID:
    try:
        return UUID(str(request_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Mã yêu cầu tạm ứng không hợp lệ. Vui lòng tải lại trang.") from exc


def _transfer_details(member: dict, month_start: date, amount: Decimal) -> dict:
    account = re.sub(r"\s+", "", str(member.get("bank_account") or ""))
    bank_name = str(member.get("bank_name") or "").strip()
    recipient_name = str(member.get("full_name") or member.get("username") or "").strip()
    if not account or not bank_name:
        raise ValueError("Nhân sự chưa có đủ số tài khoản và tên ngân hàng. Vui lòng cập nhật trong Người dùng.")
    if not re.fullmatch(r"[A-Za-z0-9]{1,50}", account):
        raise ValueError("Số tài khoản ngân hàng của nhân sự không hợp lệ. Vui lòng cập nhật trong Người dùng.")
    if not recipient_name:
        raise ValueError("Nhân sự chưa có tên người nhận chuyển khoản.")
    code = bank_code(bank_name)
    transfer_content = re.sub(
        r"\s+", " ", remove_accents(f"Tam ung luong {month_start:%m/%Y} {recipient_name}")
    ).strip()[:50]
    params = urlencode({
        "amount": str(int(amount)),
        "addInfo": transfer_content,
        "accountName": remove_accents(recipient_name).upper(),
    })
    return {
        "qr_url": f"https://img.vietqr.io/image/{quote(code, safe='')}-{quote(account, safe='')}-compact2.png?{params}",
        "recipient_name": recipient_name,
        "bank_name": bank_name,
        "bank_account": account,
        "transfer_content": transfer_content,
    }


def _record(row: dict | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    result["id"] = str(result["id"])
    return result


def _member(cursor, staff_id: int, company_id: int | None, *, lock: bool = False) -> dict | None:
    if company_id is None:
        return None
    cursor.execute(
        f"""SELECT u.id AS user_id, u.username, u.full_name, u.bank_account,
                   u.bank_name, c.id AS company_id
              FROM public.users u
              JOIN public.invoice_companies c ON c.id = ANY(u.company_ids)
             WHERE u.id = %s AND u.full_name IS NOT NULL AND c.id = %s
             {"FOR UPDATE OF u" if lock else ""}""",
        (staff_id, company_id),
    )
    return cursor.fetchone()


def get_staff_salary_advance(
    staff_id: int, month_start: date, request_id: str, company_id: int | None,
) -> dict | None:
    """Read a saved QR only while its recipient remains in the selected company."""
    token = _request_uuid(request_id)
    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            member = _member(cursor, staff_id, company_id)
            if not member:
                return None
            cursor.execute(
                """SELECT * FROM public.staff_salary_advances
                    WHERE id = %s AND user_id = %s AND month = %s AND company_id = %s""",
                (str(token), staff_id, month_start, member["company_id"]),
            )
            return _record(cursor.fetchone())


def create_staff_salary_advance(
    staff_id: int, month_start: date, amount: Decimal, request_id: str,
    company_id: int | None, created_by: int | None,
) -> dict:
    """Add one advance without overwriting any other monthly payroll values.

    The UUID is both the saved advance ID and an idempotency token. The user
    lock serializes distinct advances, including the first one for a month;
    the token lock also serializes accidentally reused tokens across users.
    """
    amount = _validate_amount(amount)
    token = _request_uuid(request_id)
    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (int.from_bytes(token.bytes[:8], byteorder="big", signed=True),),
            )
            member = _member(cursor, staff_id, company_id, lock=True)
            if not member:
                raise ValueError("Không tìm thấy nhân sự thuộc công ty.")

            cursor.execute("SELECT * FROM public.staff_salary_advances WHERE id = %s", (str(token),))
            existing = cursor.fetchone()
            if existing:
                if (existing["user_id"] != staff_id or existing["month"] != month_start
                        or existing["company_id"] != member["company_id"] or existing["amount"] != amount):
                    raise ValueError("Mã yêu cầu này đã được dùng cho khoản tạm ứng khác. Vui lòng tải lại trang.")
                return _record(existing)

            # Validate the recipient before changing the payroll or creating a record.
            details = _transfer_details(member, month_start, amount)
            cursor.execute(
                "SELECT * FROM public.staff_salary_profiles WHERE user_id = %s FOR UPDATE",
                (staff_id,),
            )
            profile = cursor.fetchone()
            if not profile:
                raise ValueError("Cần lưu hồ sơ lương nhân sự trước khi tạm ứng.")
            cursor.execute(
                """SELECT * FROM public.staff_payroll_months
                    WHERE user_id = %s AND month = %s FOR UPDATE""",
                (staff_id, month_start),
            )
            month = cursor.fetchone()
            # A month without saved attendance/earnings has no outstanding salary.
            values = {
                "work_days": Decimal(0), "standard_days": Decimal(26),
                **{key: Decimal(0) for key in (
                    "advance_amount", "trip_wage", "bonus_amount", "overtime_pay",
                    "tax_deduction", "other_deduction", "union_fee",
                )},
                **dict(month or {}),
            }
            payroll = calculate_payroll_row({
                **dict(profile), **values, "has_month_entry": month is not None,
            })
            remaining = max(payroll["net_pay"], Decimal(0))
            if amount > remaining:
                formatted = f"{remaining:,.0f}".replace(",", ".")
                raise ValueError(f"Số tiền tạm ứng vượt quá số lương còn thiếu ({formatted} đồng).")

            cursor.execute(
                """UPDATE public.staff_payroll_months
                      SET advance_amount = advance_amount + %s, updated_at = now()
                    WHERE user_id = %s AND month = %s""",
                (amount, staff_id, month_start),
            )
            cursor.execute(
                """INSERT INTO public.staff_salary_advances
                       (id, user_id, month, company_id, amount, qr_url, recipient_name,
                        bank_name, bank_account, transfer_content, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *""",
                (str(token), staff_id, month_start, member["company_id"], amount,
                 details["qr_url"], details["recipient_name"], details["bank_name"],
                 details["bank_account"], details["transfer_content"], created_by),
            )
            saved = _record(cursor.fetchone())
        conn.commit()
    return saved
