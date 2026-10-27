"""Company details used for cargo-trip invoices."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

import bcrypt
import httpx
from bs4 import BeautifulSoup
from psycopg2.extras import RealDictCursor

from backend.services.database import db


_TAX_CODE_RE = re.compile(r"[0-9]{10}(?:-?[0-9]{3})?\Z", re.ASCII)
_MAX_NAME_LENGTH = 255
_MAX_ADDRESS_LENGTH = 500
_LOOKUP_URL = "https://api.vietqr.io/v2/business/{}"
_COMPANY_COLUMNS = """c.id, c.name, c.address, c.tax_code, c.email, c.phone,
    c.bank_account, c.bank_name, c.director_user_id,
    c.signature_url, c.stamp_url, 0::numeric AS current_debt,
    c.business_activity,
    COALESCE(NULLIF(btrim(u.full_name), ''), u.username) AS director_name,
    u.username AS director_username,
    u.gender AS director_gender"""
_DIRECTOR_JOIN = "LEFT JOIN public.users u ON u.id = c.director_user_id"
_COMPANY_SELECT = f"SELECT {_COMPANY_COLUMNS} FROM public.invoice_companies c {_DIRECTOR_JOIN}"
_LOOKUP_COMPANY_COLUMNS = _COMPANY_COLUMNS.replace(
    "u.gender AS director_gender",
    "u.gender AS director_gender, u.email AS director_email, u.phone AS director_phone",
)
_LOOKUP_COMPANY_SELECT = f"SELECT {_LOOKUP_COMPANY_COLUMNS} FROM public.invoice_companies c {_DIRECTOR_JOIN}"
_PREFERRED_BANK_NAMES = (
    "VIETINBANK", "VIETCOMBANK", "TECHCOMBANK", "MBBANK", "BIDV",
    "ACB", "VPBANK", "AGRIBANK", "TPBANK", "HDBANK", "SHB",
    "VIB", "MSB", "SACOMBANK", "SEABANK",
)

BUSINESS_ACTIVITIES = (
    "Thương mại",
    "Công nghiệp chế biến, chế tạo",
    "Kinh doanh bất động sản",
    "Vận tải, kho bãi",
    "Hoạt động Tài chính, ngân hàng, bảo hiểm",
    "Giáo dục & Đào tạo",
    "Xây dựng",
    "Dịch vụ lưu trú & ăn uống",
    "Khai khoáng",
    "Nông nghiệp",
    "Ngư nghiệp",
    "Lâm nghiệp",
    "Sản xuất và phân phối điện, khí đốt, nước nóng, hơi nước và điều hòa không khí",
    "Cung cấp nước, hoạt động quản lý và xử lý rác thải",
    "Hoạt động chuyên môn, KHCN",
    "Hoạt động dịch vụ khác",
    "Hoạt động các tổ chức và cơ quan quốc tế",
    "Hoạt động hành chính và dịch vụ Hỗ trợ",
    "Hoạt động làm thuê các công việc trong các hộ gia đình, sản xuất sản phẩm vật chất và dịch vụ tự tiêu dùng của hộ gia đình",
    "Nghệ thuật, vui chơi, giải trí",
    "Thông tin & truyền thông",
    "Hoạt động của Đảng Cộng Sản tổ chức Chính Trị - Xã Hội, Quản lý nhà nước, An ninh quốc phòng, Đảm bảo xã hội bắt buộc",
    "Y tế và hoạt động trợ giúp Xã Hội",
    "Sản phẩm bán lẻ",
    "Chưa xác định mã ngành",
)


def list_business_activities() -> list[str]:
    return list(BUSINESS_ACTIVITIES)


def canonical_bank_name(value: str) -> str:
    name = str(value or "").strip()
    return name.upper() if name.upper() in _PREFERRED_BANK_NAMES else name


class TaxLookupError(Exception):
    """A lookup failed in a way the cargo form can explain to the user."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def normalize_tax_code(value: str) -> str:
    """Validate and canonicalize a Vietnamese 10- or 13-digit tax code."""

    code = str(value or "").strip()
    if not _TAX_CODE_RE.fullmatch(code):
        raise ValueError("Mã số thuế phải có 10 chữ số hoặc 10 chữ số-3 chữ số chi nhánh.")
    if len(code) == 13:
        return f"{code[:10]}-{code[10:]}"
    return code


def _clean_required_text(value: str, label: str, max_length: int) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{label} không được để trống.")
    if len(result) > max_length:
        raise ValueError(f"{label} không được vượt quá {max_length} ký tự.")
    return result




def list_companies() -> list[dict]:
    return db._execute_query(
        _COMPANY_SELECT + " ORDER BY c.name, c.id",
        fetchall=True,
        commit=False,
    )


_COMPANY_SEARCH_WHERE = """ WHERE (
    %s = ''
    OR c.tax_code = %s
    OR strpos(lower(concat_ws(' ', c.tax_code, c.name, c.address, c.email, c.phone,
        c.bank_account, c.bank_name, u.full_name, u.username)), lower(%s)) > 0
    OR EXISTS (
        SELECT 1 FROM public.company_bank_accounts a
        WHERE a.company_id = c.id
          AND strpos(lower(concat_ws(' ', a.account_number, a.bank_name, a.branch)), lower(%s)) > 0
    )
)"""
_COMPANY_DEBT_WHERE = """ AND COALESCE(a.current_debt, 0) > 0"""


def _company_select_for_owner(owner_company_id: int | None) -> tuple[str, tuple]:
    """Select partner data with only the requesting company's debt balance."""
    if not owner_company_id:
        return _COMPANY_SELECT, ()
    scoped_columns = _COMPANY_COLUMNS.replace(
        "0::numeric AS current_debt", "COALESCE(a.current_debt, 0) AS current_debt"
    )
    query = (
        f"SELECT {scoped_columns} FROM public.invoice_companies c {_DIRECTOR_JOIN} "
        "LEFT JOIN public.company_debt_accounts a "
        "ON a.company_id = c.id AND a.owner_company_id = %s"
    )
    return query, (owner_company_id,)


def _company_page_filter(query: str, debt_only: bool, seller_company_id: int | None = None) -> tuple[str, tuple]:
    term = (query or "").strip()
    where = _COMPANY_SEARCH_WHERE
    params: tuple = (term, term, term, term)
    if debt_only:
        where += _COMPANY_DEBT_WHERE if seller_company_id else " AND FALSE"
    return where, params


def count_companies(query: str = "", *, debt_only: bool = False, seller_company_id: int | None = None) -> int:
    where, params = _company_page_filter(query, debt_only, seller_company_id)
    select, owner_params = _company_select_for_owner(seller_company_id)
    row = db._execute_query(
        f"SELECT count(*) AS total FROM ({select}{where}) scoped_companies",
        owner_params + params, fetch=True, commit=False,
    )
    return int(row["total"] if row else 0)


def list_companies_page(query: str = "", *, limit: int = 20, offset: int = 0,
                        debt_only: bool = False, seller_company_id: int | None = None) -> list[dict]:
    where, params = _company_page_filter(query, debt_only, seller_company_id)
    select, owner_params = _company_select_for_owner(seller_company_id)
    return db._execute_query(
        select + where + " ORDER BY c.name, c.id LIMIT %s OFFSET %s",
        owner_params + params + (limit, offset), fetchall=True, commit=False,
    ) or []


def get_company(company_id: int) -> dict | None:
    return db._execute_query(
        _COMPANY_SELECT + " WHERE c.id = %s",
        (company_id,),
        fetch=True,
        commit=False,
    )


def find_company_by_name(name: str) -> dict | None:
    """Find a company in public.invoice_companies by 100% matching name."""
    target = str(name or "").strip().casefold()
    if not target:
        return None

    companies = db._execute_query(
        "SELECT id, name, tax_code FROM public.invoice_companies ORDER BY id",
        fetchall=True, commit=False,
    ) or []

    for c in companies:
        if str(c.get("name") or "").strip().casefold() == target:
            return c

    return None


def list_companies_for_bridge() -> list[dict]:
    """List companies for Zalo bridge 100% group name matching."""
    companies = db._execute_query(
        "SELECT id, name, tax_code FROM public.invoice_companies ORDER BY id",
        fetchall=True, commit=False,
    ) or []
    return [
        {
            "id": c["id"],
            "name": str(c["name"]).strip(),
            "tax_code": c.get("tax_code") or "",
        }
        for c in companies if c.get("name")
    ]



def get_company_bank_accounts(company_id: int) -> list[dict]:
    return db._execute_query(
        """SELECT account_number, bank_name, branch FROM public.company_bank_accounts
           WHERE company_id = %s ORDER BY sort_order, id""",
        (company_id,), fetchall=True, commit=False,
    ) or []


def list_bank_names() -> list[str]:
    rows = db._execute_query("""SELECT DISTINCT bank_name FROM (
        SELECT bank_name FROM public.company_bank_accounts
        UNION SELECT bank_name FROM public.invoice_companies
    ) banks WHERE NULLIF(btrim(bank_name), '') IS NOT NULL ORDER BY bank_name""",
        fetchall=True, commit=False,
    ) or []
    return list(dict.fromkeys([*_PREFERRED_BANK_NAMES] + [canonical_bank_name(r["bank_name"]) for r in rows if r["bank_name"]]))


def validate_company_bank_accounts(accounts: list[dict]) -> list[tuple[str, str, str]]:
    """Validate before saving company details so bad account rows cannot cause a partial save."""
    cleaned = []
    seen = set()
    for item in accounts:
        number = str(item.get("account_number") or "").strip()
        bank = canonical_bank_name(item.get("bank_name"))
        branch = str(item.get("branch") or "").strip()
        if not number and not bank and not branch:
            continue
        if not number or not bank:
            raise ValueError("Mỗi tài khoản cần có số tài khoản và ngân hàng.")
        if len(number) > 100 or len(bank) > 255 or len(branch) > 255:
            raise ValueError("Thông tin ngân hàng quá dài.")
        if number in seen:
            raise ValueError("Số tài khoản ngân hàng bị trùng trong cùng đơn vị.")
        seen.add(number)
        cleaned.append((number, bank, branch))
    return cleaned


def replace_company_bank_accounts(company_id: int, accounts: list[dict]) -> None:
    """Replace a company's accounts atomically and keep the legacy primary fields in sync."""
    cleaned = validate_company_bank_accounts(accounts)
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM public.invoice_companies WHERE id = %s FOR UPDATE", (company_id,))
            if not cursor.fetchone():
                raise ValueError("Không tìm thấy đơn vị.")
            cursor.execute("DELETE FROM public.company_bank_accounts WHERE company_id = %s", (company_id,))
            for order, (number, bank, branch) in enumerate(cleaned):
                cursor.execute("""INSERT INTO public.company_bank_accounts
                    (company_id, account_number, bank_name, branch, sort_order)
                    VALUES (%s, %s, %s, %s, %s)""", (company_id, number, bank, branch, order))
            primary = cleaned[0] if cleaned else None
            cursor.execute("""UPDATE public.invoice_companies
                SET bank_account = %s, bank_name = %s, updated_at = NOW() WHERE id = %s""",
                (primary[0] if primary else "", primary[1] if primary else "", company_id))
        conn.commit()



async def lookup_masothue_company(tax_code: str, current_name: str) -> dict:
    """Read the company and legal representative from its MaSoThue page."""
    code = normalize_tax_code(tax_code)
    ascii_name = unicodedata.normalize("NFKD", current_name.replace("Đ", "D").replace("đ", "d"))
    slug = re.sub(r"[^a-z0-9]+", "-", "".join(
        char for char in ascii_name.lower() if not unicodedata.combining(char)
    )).strip("-")
    url = f"https://masothue.com/{quote(code, safe='-')}-{slug}"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.RequestError as exc:
        raise TaxLookupError("Không kết nối được MaSoThue; thông tin đơn vị chưa thay đổi.", 503) from exc
    if response.status_code != 200:
        raise TaxLookupError("MaSoThue không tìm thấy trang đơn vị; thông tin chưa thay đổi.", 502)

    table = BeautifulSoup(response.text, "html.parser").select_one("table.table-taxinfo")
    name_cell = table.select_one('th[itemprop="name"]') if table else None
    code_cell = table.select_one('td[itemprop="taxID"]') if table else None
    address_cell = table.select_one("#tax-address-html") if table else None
    if not name_cell or not code_cell or not address_cell or code_cell.get_text(strip=True) != code:
        raise TaxLookupError("Không đọc được đúng mã số thuế trên MaSoThue; thông tin chưa thay đổi.")
    name = _clean_required_text(name_cell.get_text(" ", strip=True), "Tên đơn vị", _MAX_NAME_LENGTH)
    address = _clean_required_text(address_cell.get_text(" ", strip=True), "Địa chỉ Thuế", _MAX_ADDRESS_LENGTH)
    representative = table.select_one('tr[itemprop="alumni"] [itemprop="name"]')
    business_activity = ""
    for row in table.select("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue
        label = _normalized_person_name(cells[0].get_text(" ", strip=True))
        if representative is None and label in {"người đại diện", "người đại diện pháp luật", "đại diện pháp luật"}:
            representative = cells[1].select_one('[itemprop="name"], a[href*="type=legalName"]') or cells[1]
        if not business_activity and any(k in label for k in ("ngành nghề chính", "ngành nghề kinh doanh", "ngành nghề")):
            act_text = cells[1].get_text(" ", strip=True)
            act_text = re.sub(r"^\d+\s*[-–:]\s*", "", act_text).strip()
            if act_text and _normalized_person_name(act_text) not in {"đang cập nhật", "chưa cập nhật", "không có thông tin", "n/a", "--", "-"}:
                business_activity = act_text[:500]

    representative_name = " ".join(representative.get_text(" ", strip=True).split()) if representative else ""
    if _normalized_person_name(representative_name) in {"đang cập nhật", "chưa cập nhật", "không có thông tin", "n/a", "--", "-"}:
        representative_name = ""
    if representative_name:
        representative_name = _clean_required_text(representative_name, "Người đại diện", _MAX_NAME_LENGTH)
    return {
        "tax_code": code,
        "name": name,
        "address": address,
        "representative_name": representative_name,
        "business_activity": business_activity,
    }


def _normalized_person_name(value: str | None) -> str:
    return unicodedata.normalize("NFC", " ".join((value or "").split())).casefold()


def _director_account_names(name: str, tax_code: str) -> tuple[str, str]:
    """Use the representative's name and tax code for a deterministic account."""
    code = normalize_tax_code(tax_code)
    name = unicodedata.normalize("NFC", " ".join(name.split()))
    name = _clean_required_text(name, "Người đại diện", _MAX_NAME_LENGTH)
    ascii_name = unicodedata.normalize("NFKD", name.replace("Đ", "D").replace("đ", "d"))
    username = re.sub(r"[^a-z0-9]+", "_", "".join(
        char for char in ascii_name.lower() if not unicodedata.combining(char)
    )).strip("_") or "user"
    tax_suffix = code.replace("-", "")
    username = username[:64 - len(tax_suffix) - 1].rstrip("_")
    return name, f"{username}_{tax_suffix}"


def _ensure_director_profile_cursor(cursor, user_id: int) -> None:
    """Ensure that the company director has a salary profile with default Giám đốc title."""
    cursor.execute(
        """INSERT INTO public.staff_salary_profiles (user_id, job_title, department, updated_at)
           VALUES (%s, 'Giám đốc', 'Ban Giám đốc', NOW())
           ON CONFLICT (user_id) DO UPDATE SET
               job_title = CASE 
                   WHEN public.staff_salary_profiles.job_title IS NULL 
                        OR TRIM(public.staff_salary_profiles.job_title) IN ('', '--', '-') 
                   THEN 'Giám đốc' 
                   ELSE public.staff_salary_profiles.job_title 
               END,
               department = CASE 
                   WHEN public.staff_salary_profiles.department IS NULL 
                        OR TRIM(public.staff_salary_profiles.department) IN ('', '--', '-') 
                   THEN 'Ban Giám đốc' 
                   ELSE public.staff_salary_profiles.department 
               END""",
        (user_id,),
    )


def _ensure_director_profile_db(user_id: int | None) -> None:
    if not user_id:
        return
    db._execute_query(
        """INSERT INTO public.staff_salary_profiles (user_id, job_title, department, updated_at)
           VALUES (%s, 'Giám đốc', 'Ban Giám đốc', NOW())
           ON CONFLICT (user_id) DO UPDATE SET
               job_title = CASE 
                   WHEN public.staff_salary_profiles.job_title IS NULL 
                        OR TRIM(public.staff_salary_profiles.job_title) IN ('', '--', '-') 
                   THEN 'Giám đốc' 
                   ELSE public.staff_salary_profiles.job_title 
               END,
               department = CASE 
                   WHEN public.staff_salary_profiles.department IS NULL 
                        OR TRIM(public.staff_salary_profiles.department) IN ('', '--', '-') 
                   THEN 'Ban Giám đốc' 
                   ELSE public.staff_salary_profiles.department 
               END""",
        (user_id,),
        commit=True,
    )


def _resolve_tax_director(cursor, representative_name: str, tax_code: str,
                          company_id: int | None, current_user_id: int | None) -> tuple[int, bool]:
    """Resolve or create one canonical user inside the company refresh transaction."""
    full_name, username = _director_account_names(representative_name, tax_code)
    normalized_name = _normalized_person_name(representative_name)
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("tax-director:" + normalized_name,))
    cursor.execute("SELECT id, username, full_name, company_ids FROM public.users ORDER BY id")
    users = cursor.fetchall()
    current = next((user for user in users if user["id"] == current_user_id), None)
    if current and re.fullmatch(re.escape(username) + r"(?:_[0-9]+)?", current["username"], re.IGNORECASE):
        _ensure_director_profile_cursor(cursor, current["id"])
        return current["id"], False
    names = {normalized_name, _normalized_person_name(full_name)}
    usernames = {username.casefold(), username.rsplit("_", 1)[0].casefold()}
    matches = [user for user in users if _normalized_person_name(user.get("full_name")) in names
               or (not (user.get("full_name") or "").strip() and user["username"].casefold() in usernames)]
    preferred = next((user for user in matches if user["id"] == current_user_id), None)
    preferred = preferred or next((user for user in matches if user["username"].casefold() == username.casefold()), None)
    if preferred or len(matches) == 1:
        matched_id = (preferred or matches[0])["id"]
        _ensure_director_profile_cursor(cursor, matched_id)
        return matched_id, False

    cursor.execute("SELECT id FROM public.roles WHERE LOWER(role_name) = 'admin' ORDER BY id LIMIT 1")
    role = cursor.fetchone()
    if role is None:
        cursor.execute("SELECT id FROM public.roles ORDER BY id LIMIT 1")
        role = cursor.fetchone()

    occupied = {user["username"].casefold() for user in users}
    base_username = username
    suffix = 2
    while username.casefold() in occupied:
        username = f"{base_username}_{suffix}"
        suffix += 1
    password_hash = bcrypt.hashpw(username.encode(), bcrypt.gensalt()).decode()
    company_ids_val = [company_id] if company_id else []
    cursor.execute(
        """INSERT INTO public.users (username, full_name, hashed_password, role_id, company_ids, note)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (username, full_name, password_hash, role["id"], company_ids_val, None),
    )
    new_user_id = cursor.fetchone()["id"]
    _ensure_director_profile_cursor(cursor, new_user_id)
    return new_user_id, True


async def refresh_company_from_tax(company_id: int) -> dict:
    """Refresh tax data and its canonical director account as one atomic write."""
    company = get_company(company_id)
    if not company:
        raise TaxLookupError("Không tìm thấy đơn vị.", 404)
    fresh = await lookup_masothue_company(company["tax_code"], company["name"])
    name = _clean_required_text(fresh["name"], "Tên đơn vị", _MAX_NAME_LENGTH)
    address = _clean_required_text(fresh["address"], "Địa chỉ Thuế", _MAX_ADDRESS_LENGTH)
    created = False
    with db._get_connection() as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT id, tax_code, director_user_id FROM public.invoice_companies WHERE id = %s FOR UPDATE",
                (company_id,),
            )
            current = cursor.fetchone()
            if not current:
                raise TaxLookupError("Không tìm thấy đơn vị.", 404)
            if current["tax_code"] != fresh["tax_code"]:
                raise ValueError("Mã số thuế đã thay đổi trong lúc tra cứu. Vui lòng cập nhật lại.")
            director_id = current["director_user_id"]
            if fresh.get("representative_name"):
                director_id, created = _resolve_tax_director(
                    cursor, fresh["representative_name"], fresh["tax_code"], company_id, director_id,
                )
            update_activity_clause = ""
            activity_param = []
            if fresh.get("business_activity"):
                update_activity_clause = ", business_activity = %s"
                activity_param = [fresh["business_activity"]]
            cursor.execute(
                f"""UPDATE public.invoice_companies SET name = %s, address = %s,
                   director_user_id = %s{update_activity_clause}, updated_at = NOW() WHERE id = %s""",
                (name, address, director_id, *activity_param, company_id),
            )
            cursor.execute(_COMPANY_SELECT + " WHERE c.id = %s", (company_id,))
            updated = dict(cursor.fetchone())
        connection.commit()
    updated["director_user_created"] = created
    return updated


async def lookup_tax_code(tax_code: str) -> dict:
    """Return a saved company or VietQR details, and automatically resolve/create the director account via MaSoThue."""
    code = normalize_tax_code(tax_code)
    saved = db._execute_query(
        _LOOKUP_COMPANY_SELECT + " WHERE c.tax_code = %s",
        (code,),
        fetch=True,
        commit=False,
    )
    company_data: dict
    company_id: int | None = None

    if saved:
        company_data = dict(saved)
        company_id = company_data.get("id")
        company_data["bank_accounts"] = get_company_bank_accounts(company_id)
    else:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(_LOOKUP_URL.format(quote(code.replace("-", ""), safe="")))
        except httpx.TimeoutException as exc:
            raise TaxLookupError("Tra cứu mã số thuế quá thời gian. Bạn có thể nhập thông tin đơn vị thủ công.", 504) from exc
        except httpx.RequestError as exc:
            raise TaxLookupError("Không kết nối được dịch vụ tra cứu mã số thuế. Bạn có thể nhập thông tin đơn vị thủ công.", 503) from exc

        if response.status_code == 429:
            raise TaxLookupError("Dịch vụ tra cứu đang giới hạn lượt truy cập. Vui lòng thử lại sau hoặc nhập thủ công.", 429)
        if response.status_code != 200:
            raise TaxLookupError("Dịch vụ tra cứu mã số thuế đang lỗi. Bạn có thể nhập thông tin đơn vị thủ công.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise TaxLookupError("Dịch vụ tra cứu trả về dữ liệu không hợp lệ. Bạn có thể nhập thủ công.") from exc
        if not isinstance(payload, dict):
            raise TaxLookupError("Dịch vụ tra cứu trả về dữ liệu không hợp lệ. Bạn có thể nhập thủ công.")

        result_code = str(payload.get("code", ""))
        if result_code == "51":
            raise TaxLookupError("Mã số thuế không hợp lệ theo dịch vụ tra cứu.", 400)
        if result_code == "52":
            raise TaxLookupError("Không tìm thấy mã số thuế này. Bạn có thể nhập thông tin đơn vị thủ công.", 404)
        if result_code != "00" or not isinstance(payload.get("data"), dict):
            raise TaxLookupError("Dịch vụ tra cứu chưa trả về thông tin đơn vị. Bạn có thể nhập thủ công.")

        data = payload["data"]
        try:
            name = _clean_required_text(data.get("name"), "Tên đơn vị", _MAX_NAME_LENGTH)
            address = _clean_required_text(data.get("address"), "Địa chỉ đơn vị", _MAX_ADDRESS_LENGTH)
        except ValueError as exc:
            raise TaxLookupError("Thông tin đơn vị từ dịch vụ tra cứu chưa đầy đủ. Bạn có thể nhập thủ công.") from exc

        company_data = {
            "id": None,
            "name": name,
            "address": address,
            "tax_code": code,
            "email": str(data.get("email") or "").strip(),
            "phone": str(data.get("phone") or "").strip(),
            "business_activity": "",
            "bank_account": "",
            "bank_name": "",
            "director_user_id": None,
            "director_name": "",
            "director_username": "",
            "director_label": "",
            "bank_accounts": [],
        }

    # Automatically resolve or create director account from MaSoThue
    current_dir_id = company_data.get("director_user_id")
    director_user_created = False
    try:
        fresh = await lookup_masothue_company(code, company_data.get("name", ""))
        if fresh.get("business_activity") and not company_data.get("business_activity"):
            company_data["business_activity"] = fresh["business_activity"]
        rep_name = fresh.get("representative_name")
        if rep_name:
            with db._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    resolved_id, created = _resolve_tax_director(
                        cursor, rep_name, code, company_id, current_dir_id
                    )
                    director_user_created = created
                    if company_id:
                        cursor.execute(
                            "UPDATE public.invoice_companies SET director_user_id = %s, updated_at = NOW() WHERE id = %s",
                            (resolved_id, company_id),
                        )
                    cursor.execute(
                        "SELECT id, username, full_name, email, phone, bank_account, bank_name FROM public.users WHERE id = %s",
                        (resolved_id,),
                    )
                    u_row = cursor.fetchone()
                conn.commit()
            if u_row:
                company_data["director_user_id"] = u_row["id"]
                company_data["director_name"] = u_row["full_name"] or u_row["username"]
                company_data["director_username"] = u_row["username"]
                company_data["director_email"] = u_row.get("email") or ""
                company_data["director_phone"] = u_row.get("phone") or ""
                company_data["director_bank_account"] = u_row.get("bank_account") or ""
                company_data["director_bank_name"] = u_row.get("bank_name") or ""
    except Exception:
        # Fallback if MaSoThue is unreachable or does not have representative
        if current_dir_id and not company_data.get("director_username"):
            u_row = db._execute_query(
                "SELECT id, username, full_name, email, phone, bank_account, bank_name FROM public.users WHERE id = %s",
                (current_dir_id,),
                fetch=True,
                commit=False,
            )
            if u_row:
                company_data["director_name"] = u_row["full_name"] or u_row["username"]
                company_data["director_username"] = u_row["username"]
                company_data["director_email"] = u_row.get("email") or ""
                company_data["director_phone"] = u_row.get("phone") or ""
                company_data["director_bank_account"] = u_row.get("bank_account") or ""
                company_data["director_bank_name"] = u_row.get("bank_name") or ""

    dir_name = str(company_data.get("director_name") or "").strip()
    dir_uname = str(company_data.get("director_username") or "").strip()
    if dir_name and dir_uname and dir_name != dir_uname:
        company_data["director_label"] = f"{dir_name} ({dir_uname})"
    else:
        company_data["director_label"] = dir_name or dir_uname

    company_data["director_user_created"] = director_user_created
    return company_data


def _validate_director_user_id(value: int | str | None) -> int | None:
    """Accept only an existing account; names never create a director identity."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("Vui lòng chọn giám đốc từ danh sách Người dùng.")
    user_id = int(value)
    if user_id > 2_147_483_647:
        raise ValueError("Tài khoản giám đốc không hợp lệ.")
    user = db._execute_query(
        "SELECT id FROM public.users WHERE id = %s", (user_id,), fetch=True, commit=False,
    )
    if not user:
        raise ValueError("Tài khoản giám đốc không tồn tại. Vui lòng chọn lại.")
    return user_id


def save_company(
    tax_code: str,
    name: str,
    address: str,
    email: str = "",
    phone: str = "",
    bank_account: str | None = None,
    director_user_id: int | str | None = None,
    bank_name: str | None = None,
    signature_url: str | None = None,
    stamp_url: str | None = None,
    business_activity: str | None = None,
) -> dict:
    """Create or update a company keyed by its canonical tax code."""

    code = normalize_tax_code(tax_code)
    clean_name = _clean_required_text(name, "Tên đơn vị", _MAX_NAME_LENGTH)
    clean_address = _clean_required_text(address, "Địa chỉ đơn vị", _MAX_ADDRESS_LENGTH)
    clean_email = email.strip()
    clean_phone = phone.strip() if phone else ""
    clean_bank = bank_account.strip() if bank_account is not None else None
    clean_bank_name = bank_name.strip() if bank_name is not None else None
    clean_director = _validate_director_user_id(director_user_id)
    clean_activity = business_activity.strip() if business_activity is not None else None
    res = db._execute_query(
        f"""WITH saved AS (
           INSERT INTO public.invoice_companies (tax_code, name, address, email, phone, bank_account, director_user_id, bank_name, signature_url, stamp_url, business_activity)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (tax_code) DO UPDATE SET
               name = EXCLUDED.name,
               address = EXCLUDED.address,
               email = CASE WHEN EXCLUDED.email <> '' THEN EXCLUDED.email ELSE invoice_companies.email END,
               phone = CASE WHEN EXCLUDED.phone <> '' THEN EXCLUDED.phone ELSE invoice_companies.phone END,
               bank_account = COALESCE(EXCLUDED.bank_account, invoice_companies.bank_account),
               bank_name = COALESCE(EXCLUDED.bank_name, invoice_companies.bank_name),
               director_user_id = COALESCE(EXCLUDED.director_user_id, invoice_companies.director_user_id),
               signature_url = COALESCE(EXCLUDED.signature_url, invoice_companies.signature_url),
               stamp_url = COALESCE(EXCLUDED.stamp_url, invoice_companies.stamp_url),
               business_activity = COALESCE(EXCLUDED.business_activity, invoice_companies.business_activity),
               updated_at = NOW()
           RETURNING *
           ) SELECT {_COMPANY_COLUMNS} FROM saved c {_DIRECTOR_JOIN}""",
        (code, clean_name, clean_address, clean_email, clean_phone, clean_bank, clean_director, clean_bank_name, signature_url, stamp_url, clean_activity),
        fetch=True,
        commit=True,
    )
    if clean_director:
        _ensure_director_profile_db(clean_director)
    return res


def update_company(
    company_id: int,
    tax_code: str,
    name: str,
    address: str,
    email: str = "",
    phone: str = "",
    bank_account: str = "",
    director_user_id: int | str | None = None,
    bank_name: str = "",
    signature_url: str | None = None,
    stamp_url: str | None = None,
    remove_signature: bool = False,
    remove_stamp: bool = False,
    business_activity: str | None = None,
) -> dict:
    code = normalize_tax_code(tax_code)
    clean_name = _clean_required_text(name, "Tên đơn vị", _MAX_NAME_LENGTH)
    clean_address = _clean_required_text(address, "Địa chỉ đơn vị", _MAX_ADDRESS_LENGTH)
    clean_phone = phone.strip() if phone else ""
    clean_director = _validate_director_user_id(director_user_id)
    duplicate = db._execute_query(
        "SELECT id FROM public.invoice_companies WHERE tax_code = %s AND id <> %s",
        (code, company_id), fetch=True, commit=False,
    )
    if duplicate:
        raise ValueError("Mã số thuế đã thuộc đơn vị khác.")

    sig_clause = ", signature_url = NULL" if remove_signature else (", signature_url = %s" if signature_url is not None else "")
    stamp_clause = ", stamp_url = NULL" if remove_stamp else (", stamp_url = %s" if stamp_url is not None else "")

    extra_params = []
    if not remove_signature and signature_url is not None:
        extra_params.append(signature_url)
    if not remove_stamp and stamp_url is not None:
        extra_params.append(stamp_url)

    clean_activity = business_activity.strip() if business_activity is not None else None
    params = (code, clean_name, clean_address, email.strip(), clean_phone, bank_account.strip(), clean_director, bank_name.strip(), clean_activity, *extra_params, company_id)

    updated = db._execute_query(
        f"""WITH saved AS (
           UPDATE public.invoice_companies SET tax_code = %s, name = %s,
           address = %s, email = %s, phone = %s, bank_account = %s, director_user_id = %s, bank_name = %s,
           business_activity = %s{sig_clause}{stamp_clause}, updated_at = NOW()
           WHERE id = %s RETURNING *
           ) SELECT {_COMPANY_COLUMNS} FROM saved c {_DIRECTOR_JOIN}""",
        params,
        fetch=True, commit=True,
    )
    if not updated:
        raise ValueError("Không tìm thấy đơn vị.")
    if clean_director:
        _ensure_director_profile_db(clean_director)
    return updated


def delete_company(company_id: int) -> bool:
    used = db._execute_query(
        "SELECT 1 FROM public.cargo_trips WHERE company_id = %s LIMIT 1",
        (company_id,), fetch=True, commit=False,
    )
    if used:
        raise ValueError("Đơn vị đã có chuyến hàng; không thể xóa vì cần giữ dữ liệu hóa đơn.")
    used_invoice = db._execute_query(
        "SELECT 1 FROM public.bkav_invoices WHERE buyer_company_id = %s LIMIT 1",
        (company_id,), fetch=True, commit=False,
    )
    if used_invoice:
        raise ValueError("Đơn vị đã có hóa đơn; không thể xóa.")
    used_user = db._execute_query(
        "SELECT username FROM public.users WHERE %s = ANY(company_ids) LIMIT 1",
        (company_id,), fetch=True, commit=False,
    )
    if used_user:
        raise ValueError(f"Đơn vị đang được gán cho người dùng '{used_user['username']}'; không thể xóa.")
    return bool(db._execute_query(
        "DELETE FROM public.invoice_companies WHERE id = %s RETURNING id",
        (company_id,), fetch=True, commit=True,
    ))
