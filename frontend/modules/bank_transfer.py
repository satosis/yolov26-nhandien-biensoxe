"""Bank identifiers shared by payment QR flows."""

import re
import unicodedata


BANK_CODE_MAP = {
    "VIETINBANK": "ICB", "VIETIN BANK": "ICB", "CTG": "ICB", "ICB": "ICB",
    "VIETCOMBANK": "VCB", "VCB": "VCB",
    "TECHCOMBANK": "TCB", "TCB": "TCB",
    "MBBANK": "MB", "MB": "MB", "MB BANK": "MB",
    "BIDV": "BIDV", "ACB": "ACB",
    "VPBANK": "VPB", "VPB": "VPB",
    "AGRIBANK": "VBA", "VBA": "VBA",
    "TPBANK": "TPB", "TPB": "TPB",
    "HDBANK": "HDB", "HDB": "HDB",
    "SHB": "SHB", "VIB": "VIB", "MSB": "MSB",
    "SACOMBANK": "STB", "STB": "STB",
    "SEABANK": "SEAB", "SEAB": "SEAB",
}


def remove_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in normalized if not unicodedata.combining(char)).replace("đ", "d").replace("Đ", "D")


def bank_code(value: str) -> str:
    name = " ".join(remove_accents(value).upper().split())
    code = BANK_CODE_MAP.get(name) or BANK_CODE_MAP.get(name.replace(" ", ""))
    if code:
        return code
    if re.fullmatch(r"[0-9]{6}", name):
        return name
    raise ValueError("Ngân hàng của nhân sự chưa hợp lệ. Cập nhật tên ngân hàng hoặc mã BIN trong Người dùng.")
