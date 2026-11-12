"""Hạch toán (accounting) of one sales invoice, which the user may change on the invoice form.

By default an invoice posts Có 5113 (cước), Có 33311 (thuế GTGT) and Nợ 131 (còn nợ), 1121 (chuyển khoản)
or 1111 (tiền mặt). Only the accounts a user changed are stored, so an invoice without a row keeps
following the defaults, including when its payment status or method changes later.
"""

from __future__ import annotations

import re

from backend.services.database import db


DEFAULT_REVENUE_ACCOUNT = "5113"
DEFAULT_VAT_ACCOUNT = "33311"
DEBT_ACCOUNT = "131"
BANK_ACCOUNT = "1121"
CASH_ACCOUNT = "1111"

ACCOUNT_KEYS = ("revenue_account", "vat_account", "payment_account")

# The books read these accounts, so each one is limited to the group it belongs to (TT133/2016/TT-BTC).
REVENUE_PREFIXES = ("511",)
VAT_PREFIXES = ("3331",)
DEBT_PREFIXES = ("131", "138")
PAID_PREFIXES = ("111", "112")

_TABLE = "public.bkav_invoice_accounting"
def default_payment_account(payment_status: str, payment_method: str) -> str:
    if payment_status == "debt":
        return DEBT_ACCOUNT
    return CASH_ACCOUNT if payment_method == "TM" else BANK_ACCOUNT


def default_accounts(payment_status: str, payment_method: str) -> dict[str, str]:
    return {
        "revenue_account": DEFAULT_REVENUE_ACCOUNT,
        "vat_account": DEFAULT_VAT_ACCOUNT,
        "payment_account": default_payment_account(payment_status, payment_method),
    }


def _allowed_prefixes(key: str, payment_status: str) -> tuple[tuple[str, ...], str]:
    if key == "revenue_account":
        return REVENUE_PREFIXES, "Tài khoản doanh thu phải thuộc nhóm 511 (ví dụ 5113)."
    if key == "vat_account":
        return VAT_PREFIXES, "Tài khoản thuế GTGT đầu ra phải thuộc nhóm 3331 (ví dụ 33311)."
    if payment_status == "debt":
        return DEBT_PREFIXES, "Hóa đơn còn nợ phải ghi Nợ tài khoản phải thu (131 hoặc 138)."
    return PAID_PREFIXES, "Hóa đơn đã trả tiền phải ghi Nợ tiền mặt (111x) hoặc tiền gửi ngân hàng (112x)."


def valid_account(key: str, value: str, payment_status: str) -> bool:
    prefixes, _ = _allowed_prefixes(key, payment_status)
    return bool(re.fullmatch(r"[0-9]{3,6}", value)) and value.startswith(prefixes)


def parse_accounts(form: dict, payment_status: str, payment_method: str) -> dict[str, str]:
    """The three accounts of a submitted form; an empty or missing one means the default."""
    accounts = {}
    for key, default in default_accounts(payment_status, payment_method).items():
        value = str(form.get(key) or "").strip() or default
        if not re.fullmatch(r"[0-9]{3,6}", value):
            raise ValueError("Tài khoản hạch toán chỉ gồm 3 đến 6 chữ số.")
        prefixes, message = _allowed_prefixes(key, payment_status)
        if not value.startswith(prefixes):
            raise ValueError(message)
        accounts[key] = value
    return accounts


def book_for_payment_account(payment_account: str, payment_method: str) -> str:
    """Sổ ngân hàng or sổ quỹ tiền mặt that receives a paid invoice: 111x is cash, 112x is bank."""
    if payment_account.startswith("111"):
        return "cash"
    if payment_account.startswith("112"):
        return "bank"
    return "bank" if payment_method == "CK" else "cash"


def effective_accounts(payment_status: str, payment_method: str, saved: dict | None) -> dict[str, str]:
    """Defaults with the user's saved changes on top; a saved account that no longer fits is ignored."""
    accounts = default_accounts(payment_status, payment_method)
    for key in ACCOUNT_KEYS:
        value = str((saved or {}).get(key) or "").strip()
        if value and valid_account(key, value, payment_status):
            accounts[key] = value
    return accounts


def save_accounts(cursor, invoice_guid: str, accounts: dict[str, str], payment_status: str, payment_method: str) -> None:
    """Store only the accounts that differ from the defaults; none different means no row."""
    defaults = default_accounts(payment_status, payment_method)
    changed = {key: value for key, value in accounts.items() if value != defaults[key]}
    if not changed:
        cursor.execute(f"DELETE FROM {_TABLE} WHERE invoice_guid = %s", (invoice_guid,))
        return
    cursor.execute(f"""
        INSERT INTO {_TABLE} (invoice_guid, revenue_account, vat_account, payment_account, updated_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (invoice_guid) DO UPDATE SET
            revenue_account = EXCLUDED.revenue_account, vat_account = EXCLUDED.vat_account,
            payment_account = EXCLUDED.payment_account, updated_at = now()
    """, (invoice_guid, changed.get("revenue_account"), changed.get("vat_account"), changed.get("payment_account")))


def load_saved_accounts(invoice_guid: str) -> dict:
    """The accounts a user changed on one invoice ({} when none)."""
    row = db._execute_query(
        f"SELECT revenue_account, vat_account, payment_account FROM {_TABLE} WHERE invoice_guid = %s",
        (str(invoice_guid),), fetch=True, commit=False)
    return dict(row) if row else {}


def accounts_for_invoice(invoice: dict) -> dict[str, str]:
    saved = load_saved_accounts(invoice["invoice_guid"])
    return effective_accounts(invoice.get("payment_status") or "paid", invoice.get("payment_method") or "CK", saved)
