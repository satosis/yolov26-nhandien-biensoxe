"""Parser for the sales/debt workbook format used by the legacy Excel ledger."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from hashlib import sha256
from io import BytesIO
import ast
import operator
from pathlib import PurePosixPath
import re
import unicodedata
import zipfile

import openpyxl


MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_ARCHIVE_UNPACKED_BYTES = 160 * 1024 * 1024
MAX_WORKBOOKS = 20
MAX_ROWS_PER_SHEET = 100_000


class SalesImportError(ValueError):
    pass


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").replace("Đ", "D").replace("đ", "d"))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _decimal(value: object) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).strip().replace("đ", "").replace("Đ", "").replace(" ", "")
    if not text or text in ("-", "–", "—", "None", "null", "N/A", "n/a", "--"):
        return Decimal(0)
    # The workbook uses comma or dot thousands separators. The last separator
    # is treated as decimal only when followed by one or two digits.
    if "," in text and "." in text:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in text:
        tail = text.rsplit(",", 1)[-1]
        text = text.replace(",", ".") if len(tail) in (1, 2) else text.replace(",", "")
    elif "." in text:
        tail = text.rsplit(".", 1)[-1]
        if len(tail) == 3 and len(text.rsplit(".", 1)[0]) >= 1:
            text = text.replace(".", "")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise SalesImportError(f"Giá trị số không hợp lệ: {value}") from exc
    if not result.is_finite():
        raise SalesImportError("File có giá trị số không hữu hạn.")
    return result


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 20000 <= value <= 80000:
            try:
                from openpyxl.utils.datetime import from_excel
                dt = from_excel(value)
                if isinstance(dt, (datetime, date)):
                    return dt.date() if isinstance(dt, datetime) else dt
            except Exception:
                pass
    if isinstance(value, str):
        raw = value.strip()
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%y", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return None


def _safe_formula(formula: object) -> Decimal | None:
    """Evaluate only arithmetic formulas, as a fallback when cached Excel values are absent."""
    if not isinstance(formula, str) or not formula.startswith("="):
        return None
    expression = formula[1:].strip()
    if not re.fullmatch(r"[\d\s.+\-*/()]+", expression):
        return None
    try:
        tree = ast.parse(expression, mode="eval")

        def visit(node):
            if isinstance(node, ast.Expression):
                return visit(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return Decimal(str(node.value))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = visit(node.operand)
                return value if isinstance(node.op, ast.UAdd) else -value
            if isinstance(node, ast.BinOp):
                left, right = visit(node.left), visit(node.right)
                operations = {ast.Add: operator.add, ast.Sub: operator.sub,
                              ast.Mult: operator.mul, ast.Div: operator.truediv}
                operation = operations.get(type(node.op))
                if operation:
                    return operation(left, right)
            raise ValueError

        result = visit(tree)
        return result if result.is_finite() else None
    except Exception:
        return None


def _cell_value(sheet, formula_sheet, row: int, column: int):
    value = sheet.cell(row=row, column=column).value
    if value is not None:
        return value
    formula = formula_sheet.cell(row=row, column=column).value
    evaluated = _safe_formula(formula)
    return evaluated if evaluated is not None else formula


def _headers(ws, formula_ws, required: tuple[str, ...], search_rows: int = 12):
    aliases = {
        "invoice": ("stt", "sohd", "sohoadon", "soct", "chungtu", "sochungtu"),
        "date": ("ngaythang", "ngay", "ngayban", "ngayct", "ngaychungtu", "ngayghi"),
        "code": ("makh", "mkh", "makhachhang", "makhach"),
        "name": ("tenkh", "tenkhachhang", "tennguoi", "tenkhach", "khachhang", "hoten", "tencanhan"),
        "product": ("tenhanghoa", "tenhang", "sanpham", "mathang", "noidung", "diengiai"),
        "unit": ("dvt", "donvi"),
        "quantity": ("soluong", "sl"),
        "price": ("dongia", "giaban", "gia"),
        "amount": ("thanhtien", "tienhang", "notienhang", "tongtien", "sotien"),
        "rounded": ("lamtron", "congtienhang"),
        "note": ("ghichu", "diengiai", "ghichuthem"),
        "opening": ("sodauki", "dauky", "tondau", "sodudauky", "dudauky", "sodauky", "nodauky"),
        "due": ("notienhang", "no", "phatsinh", "tiengoc", "sotienno", "phatsinhtang"),
        "paid": ("thanhtoan", "sotienthanhtoan", "dathanhtoan", "datra", "sotientra", "tientra", "phatsinhgiam"),
        "source": ("nguonnhan", "nguonthu", "nguon", "phuongthuc", "hinhthuc"),
        "phone": ("sdt", "dienthoai", "phone", "sodienthoai"),
        "address": ("diachi", "diachikh"),
        "weigh_code": ("macan", "sophieucan", "phieucan"),
        "gross_weight": ("cantong", "tongcan", "tongkg", "klcantong"),
        "tare_weight": ("bi", "canbi", "trongluongbi", "klbi"),
    }
    aliases = {key: tuple(_norm(item) for item in values) for key, values in aliases.items()}
    for row in range(1, min(ws.max_row, search_rows) + 1):
        found = {}
        for col in range(1, min(ws.max_column, 40) + 1):
            label = _norm(_cell_value(ws, formula_ws, row, col))
            if not label:
                continue
            for key, candidates in aliases.items():
                if label in candidates or any(len(c) >= 5 and c in label for c in candidates):
                    found[key] = col
        if all(key in found for key in required):
            return row, found
    return None, {}


def _customer_code(code: str, name: str) -> str:
    code = code.strip()
    if code:
        return code[:40]
    digest = sha256(_norm(name).encode("utf-8")).hexdigest()[:20]
    return "IMP-" + digest


def _stable_key(*parts: object) -> str:
    source = "\x1f".join(str(part or "").strip() for part in parts)
    return sha256(source.encode("utf-8")).hexdigest()


def _workbook_bytes(filename: str, raw: bytes) -> list[tuple[str, bytes]]:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise SalesImportError("File vượt quá giới hạn 40 MB.")
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.casefold()
    if suffix in (".xlsx", ".xlsm"):
        return [(filename, raw)]
    if suffix != ".zip":
        raise SalesImportError("Chỉ nhận file .xlsx, .xlsm hoặc .zip chứa các file Excel.")
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            files = []
            unpacked_size = 0
            for info in archive.infolist():
                path = PurePosixPath(info.filename.replace("\\", "/"))
                if info.is_dir() or any(part.startswith(".") or part == "__MACOSX" for part in path.parts):
                    continue
                if path.suffix.casefold() not in (".xlsx", ".xlsm"):
                    continue
                unpacked_size += info.file_size
                if unpacked_size > MAX_ARCHIVE_UNPACKED_BYTES:
                    raise SalesImportError("Tổng dung lượng giải nén ZIP vượt quá 160 MB.")
                if info.file_size > MAX_UPLOAD_BYTES:
                    raise SalesImportError(f"File {path.name} bên trong ZIP vượt quá 40 MB.")
                files.append((path.name, archive.read(info)))
                if len(files) > MAX_WORKBOOKS:
                    raise SalesImportError(f"ZIP chỉ được chứa tối đa {MAX_WORKBOOKS} file Excel.")
            if not files:
                raise SalesImportError("ZIP không chứa file .xlsx hoặc .xlsm.")
            return files
    except zipfile.BadZipFile as exc:
        raise SalesImportError("File ZIP bị lỗi hoặc không đọc được.") from exc


def parse_upload(filename: str, raw: bytes) -> dict:
    customers: dict[str, dict] = {}
    sales: dict[tuple, dict] = {}
    ledger_rows: list[dict] = []
    warnings: list[str] = []
    workbook_count = 0
    processed_payloads: set[str] = set()

    for member_name, payload in _workbook_bytes(filename, raw):
        payload_hash = sha256(payload).hexdigest()
        if payload_hash in processed_payloads:
            warnings.append(f"{member_name}: nội dung trùng file đã có trong ZIP nên bỏ qua.")
            continue
        processed_payloads.add(payload_hash)
        try:
            wb_values = openpyxl.load_workbook(BytesIO(payload), data_only=True, read_only=False)
            wb_formulas = wb_values
        except Exception as exc:
            raise SalesImportError(f"Không mở được file Excel {member_name}: {exc}") from exc
        try:
            names = {_norm(name): name for name in wb_values.sheetnames}

            def _find_sheet(*candidates):
                for cand in candidates:
                    norm_c = _norm(cand)
                    if norm_c in names:
                        return names[norm_c]
                return None

            sales_name = _find_sheet("SBH", "SO BAN HANG", "BAN HANG", "SALES")
            customer_name = _find_sheet("CONG NO", "SO CONG NO", "KHACH HANG", "DS KHACH HANG", "CUSTOMERS")
            ledger_name = _find_sheet("NHAT KI CONG NO", "NHAT KY CONG NO", "NHAT KY", "NHAT KI", "LEDGER")

            # Fallback for single-sheet workbooks:
            if not sales_name and not customer_name and not ledger_name and len(wb_values.sheetnames) == 1:
                single_name = wb_values.sheetnames[0]
                ws, fws = wb_values[single_name], wb_formulas[single_name]
                if _headers(ws, fws, ("date", "name", "product", "quantity"))[0]:
                    sales_name = single_name
                elif _headers(ws, fws, ("date", "name"))[0] and (_headers(ws, fws, ("due",))[0] or _headers(ws, fws, ("paid",))[0]):
                    ledger_name = single_name
                elif _headers(ws, fws, ("name",))[0]:
                    customer_name = single_name

            if not sales_name and not customer_name and not ledger_name:
                warnings.append(f"{member_name}: không tìm thấy sheet SBH, CÔNG NỢ hoặc NHẬT KÝ CÔNG NỢ nên bỏ qua file.")
                continue
            workbook_count += 1

            # Import customer list
            if customer_name:
                ws, fws = wb_values[customer_name], wb_formulas[customer_name]
                header_row, cols = _headers(ws, fws, ("name",))
                if header_row:
                    for r in range(header_row + 1, ws.max_row + 1):
                        if r > MAX_ROWS_PER_SHEET:
                            raise SalesImportError("Sheet công nợ có quá nhiều dòng.")
                        name = _text(_cell_value(ws, fws, r, cols["name"]))
                        if not name or _norm(name) in ("tong", "tongcong", "total"):
                            continue
                        code = _text(_cell_value(ws, fws, r, cols["code"])) if "code" in cols else ""
                        code = _customer_code(code, name)
                        opening = _decimal(_cell_value(ws, fws, r, cols["opening"])) if "opening" in cols else Decimal(0)
                        phone = _text(_cell_value(ws, fws, r, cols["phone"])) if "phone" in cols else ""
                        address = _text(_cell_value(ws, fws, r, cols["address"])) if "address" in cols else ""
                        customers.setdefault(code.casefold(), {
                            "customer_code": code, "full_name": name[:255], "phone": phone[:50], "address": address[:500],
                            "opening_balance": opening,
                        })

            # Import sales sheet
            if sales_name:
                ws, fws = wb_values[sales_name], wb_formulas[sales_name]
                header_row, cols = _headers(ws, fws, ("date", "name", "product", "quantity"))
                if not header_row:
                    warnings.append(f"{member_name}: sheet {sales_name} thiếu cột ngày, tên khách, mặt hàng hoặc số lượng.")
                else:
                    for r in range(header_row + 1, ws.max_row + 1):
                        if r > MAX_ROWS_PER_SHEET:
                            raise SalesImportError(f"Sheet {sales_name} có quá nhiều dòng.")
                        invoice = _text(_cell_value(ws, fws, r, cols["invoice"])) if "invoice" in cols else f"ROW-{r}"
                        sale_date = _date(_cell_value(ws, fws, r, cols["date"]))
                        code = _text(_cell_value(ws, fws, r, cols["code"])) if "code" in cols else ""
                        name = _text(_cell_value(ws, fws, r, cols["name"]))
                        product = _text(_cell_value(ws, fws, r, cols["product"]))
                        if not sale_date or not name or not product:
                            continue
                        code = _customer_code(code, name)
                        key = (sale_date.isoformat(), invoice, code.casefold())
                        customer = customers.setdefault(code.casefold(), {
                            "customer_code": code, "full_name": name[:255], "phone": "", "address": "",
                            "opening_balance": Decimal(0),
                        })
                        try:
                            quantity = _decimal(_cell_value(ws, fws, r, cols["quantity"]))
                            price = _decimal(_cell_value(ws, fws, r, cols["price"])) if "price" in cols else Decimal(0)
                            raw_amount = _decimal(_cell_value(ws, fws, r, cols["amount"])) if "amount" in cols else quantity * price
                            if price == 0 and quantity > 0 and raw_amount > 0:
                                price = raw_amount / quantity
                            rounded = _decimal(_cell_value(ws, fws, r, cols["rounded"])) if "rounded" in cols else Decimal(0)
                            line_total = rounded if rounded > 0 else raw_amount
                            if line_total <= 0:
                                line_total = quantity * price
                            if line_total > 0 and line_total % 1000:
                                line_total = (line_total / 1000).to_integral_value(rounding=ROUND_CEILING) * 1000
                            if quantity <= 0 or price < 0 or line_total < 0:
                                raise ValueError
                        except (ValueError, ArithmeticError, InvalidOperation, SalesImportError):
                            warnings.append(f"{member_name}, dòng {r}: bỏ qua do số lượng hoặc đơn giá không hợp lệ.")
                            continue
                        sale = sales.setdefault(key, {
                            "source_invoice_number": invoice[:100], "sale_date": sale_date.isoformat(),
                            "customer_code": customer["customer_code"], "customer_name": name[:255],
                            "customer_phone": customer["phone"], "customer_address": customer["address"],
                            "carrier": "", "note": "", "items": [], "total_amount": Decimal(0),
                            "paid_amount": Decimal(0), "payment_source": "", "source_key": "",
                        })
                        gross_val = _decimal(_cell_value(ws, fws, r, cols["gross_weight"])) if "gross_weight" in cols else None
                        tare_val = _decimal(_cell_value(ws, fws, r, cols["tare_weight"])) if "tare_weight" in cols else None
                        item = {
                            "product_name": product[:255],
                            "unit": _text(_cell_value(ws, fws, r, cols["unit"]))[:80] if "unit" in cols else "",
                            "weigh_code": _text(_cell_value(ws, fws, r, cols["weigh_code"]))[:100] if "weigh_code" in cols else "",
                            "gross_weight": gross_val,
                            "tare_weight": tare_val,
                            "quantity": quantity,
                            "unit_price": price,
                            "line_total": line_total,
                        }
                        sale["items"].append(item)
                        sale["total_amount"] += line_total
                        row_note = _text(_cell_value(ws, fws, r, cols["note"])) if "note" in cols else ""
                        if row_note and row_note not in sale["note"]:
                            sale["note"] = (sale["note"] + ("; " if sale["note"] else "") + row_note)[:2000]

            # Import debt transaction ledger
            if ledger_name:
                lws, lfws = wb_values[ledger_name], wb_formulas[ledger_name]
                header_row, lcols = _headers(lws, lfws, ("date", "name"))
                if header_row and ("due" in lcols or "paid" in lcols):
                    previous_date = None
                    previous_code = ""
                    previous_name = ""
                    previous_invoice = ""
                    for r in range(header_row + 1, lws.max_row + 1):
                        if r > MAX_ROWS_PER_SHEET:
                            raise SalesImportError("Sheet nhật ký công nợ có quá nhiều dòng.")
                        name = _text(_cell_value(lws, lfws, r, lcols["name"]))
                        raw_date = _date(_cell_value(lws, lfws, r, lcols["date"]))
                        raw_code = _text(_cell_value(lws, lfws, r, lcols["code"])) if "code" in lcols else ""
                        raw_invoice = _text(_cell_value(lws, lfws, r, lcols["invoice"])) if "invoice" in lcols else ""
                        same_customer = not raw_code or not previous_code or raw_code.casefold() == previous_code.casefold()
                        invoice = raw_invoice or (
                            previous_invoice if same_customer and (raw_date is None or raw_date == previous_date) else ""
                        )
                        tx_date = raw_date
                        if name:
                            previous_name = name
                        else:
                            name = previous_name
                        if raw_code:
                            previous_code = raw_code
                        if tx_date:
                            previous_date = tx_date
                        else:
                            tx_date = previous_date
                        if raw_invoice:
                            previous_invoice = invoice
                        elif not same_customer:
                            previous_invoice = ""
                        if not name or not tx_date or _norm(name) in ("tong", "tongcong", "total"):
                            continue
                        code = _customer_code(raw_code or previous_code, name)
                        customer = customers.setdefault(code.casefold(), {
                            "customer_code": code, "full_name": name[:255], "phone": "", "address": "",
                            "opening_balance": Decimal(0),
                        })
                        try:
                            due = max(Decimal(0), _decimal(_cell_value(lws, lfws, r, lcols["due"]))) if "due" in lcols else Decimal(0)
                            paid = max(Decimal(0), _decimal(_cell_value(lws, lfws, r, lcols["paid"]))) if "paid" in lcols else Decimal(0)
                        except SalesImportError:
                            warnings.append(f"{member_name}, nhật ký dòng {r}: bỏ qua do số tiền không hợp lệ.")
                            continue
                        if due == 0 and paid == 0:
                            continue
                        ledger_rows.append({
                            "source_invoice_number": invoice[:100],
                            "transaction_date": tx_date.isoformat(), "customer_code": customer["customer_code"],
                            "customer_name": name[:255], "amount_due": due, "amount_paid": paid,
                            "received_from": _text(_cell_value(lws, lfws, r, lcols["source"]))[:255] if "source" in lcols else "",
                            "note": _text(_cell_value(lws, lfws, r, lcols["note"]))[:2000] if "note" in lcols else "",
                            "row_number": r, "file": member_name,
                        })
        finally:
            wb_values.close()
            wb_formulas.close()

    if not workbook_count:
        raise SalesImportError("Không tìm thấy dữ liệu hợp lệ trong file Excel để nhập.")
    if not customers and not sales and not ledger_rows:
        raise SalesImportError("File không chứa dữ liệu khách hàng, bán hàng hoặc nhật ký công nợ hợp lệ.")

    # Build source keys independently from the filename so re-importing the same
    # data from another ZIP or renamed workbook remains idempotent.
    for sale in sales.values():
        sale["source_key"] = _stable_key(
            "sale", sale["sale_date"], sale["source_invoice_number"], sale["customer_code"].casefold()
        )
    by_sale_identity: dict[tuple, list[dict]] = {}
    for sale in sales.values():
        identity = (sale["source_invoice_number"], sale["customer_code"].casefold())
        by_sale_identity.setdefault(identity, []).append(sale)
    for rows in by_sale_identity.values():
        rows.sort(key=lambda item: item["sale_date"])

    payments: list[dict] = []
    for row in ledger_rows:
        identity = (row["source_invoice_number"], row["customer_code"].casefold())
        candidates = by_sale_identity.get(identity, []) if row["source_invoice_number"] else []
        match = next((sale for sale in candidates
                      if sale["sale_date"] == row["transaction_date"] and
                      sale["total_amount"] == row["amount_due"] and not sale.get("ledger_linked")), None)
        if match:
            match["ledger_linked"] = True
        linked_sale = None
        if match:
            linked_sale = match
        elif candidates:
            linked_sale = min(candidates, key=lambda item: abs(
                date.fromisoformat(item["sale_date"]).toordinal() - date.fromisoformat(row["transaction_date"]).toordinal()
            ))
        elif not row["source_invoice_number"]:
            same_day_sales = [sale for sale in sales.values()
                              if sale["customer_code"].casefold() == row["customer_code"].casefold()
                              and sale["sale_date"] == row["transaction_date"]]
            exact_amount = row["amount_due"] or row["amount_paid"]
            if len(same_day_sales) == 1 and exact_amount == same_day_sales[0]["total_amount"]:
                linked_sale = same_day_sales[0]
        row["import_key"] = _stable_key(
            "ledger", row["transaction_date"], row["source_invoice_number"],
            row["customer_code"].casefold(), row["row_number"],
        )
        row["sale_source_key"] = linked_sale["source_key"] if linked_sale else None
        # Sales already create the invoice debt entry; this row contributes only
        # its receipt when it can be associated with an imported sale.
        if linked_sale:
            row["amount_due"] = Decimal(0)
            if row["amount_paid"] <= 0:
                continue
        payments.append(row)

    return {
        "customers": list(customers.values()), "sales": list(sales.values()),
        "payments": payments, "warnings": warnings, "workbook_count": workbook_count,
    }
