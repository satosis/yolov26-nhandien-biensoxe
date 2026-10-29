import logging
from backend.services.database import db
from frontend.modules.user_nicknames import clean_nickname
from frontend.modules.gender_utils import predict_gender

logger = logging.getLogger("user_repo")


def get_all_users(limit=None, offset=None) -> list[dict]:
    query = """
        SELECT u.id, u.username, r.role_name as role, u.created_at, u.full_name, u.gender, u.nickname, u.phone, u.note, u.site_ids, u.company_ids, u.person_identity, u.kyc_level,
               u.email, u.bank_account, u.bank_name,
               p.job_title, p.department
        FROM users u
        LEFT JOIN roles r ON u.role_id = r.id
        LEFT JOIN staff_salary_profiles p ON p.user_id = u.id
        ORDER BY u.id
    """
    params = []
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    return db.execute_query(query, tuple(params) if params else None, fetchall=True)


def count_all_users() -> int:
    row = db.execute_query("SELECT COUNT(*) AS total FROM users", fetch=True)
    return int((row or {}).get("total") or 0)


def get_user_by_id(user_id: int) -> dict | None:
    query = """
        SELECT u.id, u.username, u.full_name, u.gender, u.nickname, u.phone, u.role_id, u.site_ids, u.company_ids, u.note, u.person_identity,
               u.email, u.bank_account, u.bank_name, r.role_name as role,
               p.job_title, p.department
        FROM users u 
        LEFT JOIN roles r ON u.role_id = r.id
        LEFT JOIN staff_salary_profiles p ON p.user_id = u.id
        WHERE u.id = %s
    """
    return db.execute_query(query, (user_id,), fetch=True)


def create_user_record(username: str, hashed_password: str, role_name: str,
                       full_name: str = None, gender: str = None, nickname: str = "", phone: str = None, email: str = None,
                       bank_account: str = None, bank_name: str = None,
                       note: str = None, site_ids: list | None = None,
                       company_ids: list | None = None) -> tuple[bool, str]:
    existing = db.select("users", columns="id", conditions={"username": username}, fetchall=False)
    if existing:
        return False, f"Username '{username}' đã tồn tại."
    if not gender or gender not in ("nam", "nữ"):
        gender = predict_gender(full_name)
    query = """
        INSERT INTO users (username, hashed_password, role_id, full_name, gender, nickname, phone, email, bank_account, bank_name, note, site_ids, company_ids)
        VALUES (%s, %s, (SELECT id FROM roles WHERE role_name = %s LIMIT 1), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    new_id = db.execute_query(
        query, (username, hashed_password, role_name, full_name, gender, clean_nickname(nickname), phone, email, bank_account, bank_name, note, site_ids, company_ids),
        commit=True, return_id=True,
    )
    return (True, "") if new_id is not None else (False, "Không thể tạo người dùng.")


def update_user_record(user_id: int, username: str, role_name: str, hashed_password: str = None,
                       full_name: str = None, gender: str = None, nickname: str = "", phone: str = None, email: str = None,
                       bank_account: str = None, bank_name: str = None,
                       note: str = None, site_ids: list | None = None,
                       company_ids: list | None = None) -> bool:
    nickname = clean_nickname(nickname)
    if not gender or gender not in ("nam", "nữ"):
        gender = predict_gender(full_name)
    if hashed_password:
        query = """
            UPDATE users 
            SET username = %s, hashed_password = %s, role_id = (SELECT id FROM roles WHERE role_name = %s LIMIT 1),
                full_name = %s, gender = %s, nickname = %s, phone = %s, email = %s, bank_account = %s, bank_name = %s,
                note = %s, site_ids = %s, company_ids = %s, updated_at = NOW()
            WHERE id = %s
        """
        params = (username, hashed_password, role_name, full_name, gender, nickname, phone, email, bank_account, bank_name, note, site_ids, company_ids, user_id)
    else:
        query = """
            UPDATE users 
            SET username = %s, role_id = (SELECT id FROM roles WHERE role_name = %s LIMIT 1),
                full_name = %s, gender = %s, nickname = %s, phone = %s, email = %s, bank_account = %s, bank_name = %s,
                note = %s, site_ids = %s, company_ids = %s, updated_at = NOW()
            WHERE id = %s
        """
        params = (username, role_name, full_name, gender, nickname, phone, email, bank_account, bank_name, note, site_ids, company_ids, user_id)
    return db.execute_query(query, params, commit=True)


def delete_user_record(user_id: int) -> bool:
    return db.delete("users", {"id": user_id})

