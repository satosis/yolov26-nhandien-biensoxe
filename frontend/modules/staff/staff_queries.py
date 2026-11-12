"""Truy vấn cơ sở dữ liệu của frontend.modules.staff.staff_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db
from backend.services.crypto_service import encrypt_field
from frontend.modules.user_nicknames import clean_nickname


def staff_missing_select_users(user_id):
    return db._execute_query("SELECT 1 FROM public.users WHERE id = %s", (user_id,), fetch=True, commit=False)


def validate_dept_name_select_enterprise_departments(clean):
    return db._execute_query("SELECT 1 FROM public.enterprise_departments WHERE LOWER(TRIM(name)) = %s", (clean,), fetch=True, commit=False)


def username_taken(username, current_user_id=None):
    query = "SELECT id FROM public.users WHERE username = %s"
    params = [username]
    if current_user_id:
        query += " AND id != %s"
        params.append(current_user_id)
    return db._execute_query(query, tuple(params), fetch=True, commit=False)


def get_default_staff_role_id_select_roles():
    return db._execute_query(
        "SELECT id FROM public.roles WHERE LOWER(role_name) = 'admin' ORDER BY id LIMIT 1",
        fetch=True, commit=False
    )


def _staff_filter(company_id, department, search_term, private_ids):
    """WHERE của danh sách nhân sự: công ty đang chọn, phòng ban, và ô tìm kiếm (tên, tài khoản, chức danh, SĐT/CCCD đã khớp)."""
    where_clauses = ["1=1"]
    params = []
    if company_id is not None:
        where_clauses.append("%s = ANY(u.company_ids)")
        params.append(company_id)
    if department:
        where_clauses.append("LOWER(TRIM(p.department)) = LOWER(TRIM(%s))")
        params.append(department)
    if search_term is not None:
        where_clauses.append(
            "(u.full_name ILIKE %s OR u.username ILIKE %s OR u.id = ANY(%s) OR p.job_title ILIKE %s)"
        )
        params.extend([search_term, search_term, private_ids, search_term])
    return " AND ".join(where_clauses), params


def count_staff(company_id, department, search_term, private_ids):
    where_sql, params = _staff_filter(company_id, department, search_term, private_ids)
    count_sql = f"""
        SELECT COUNT(*) as total
        FROM public.users u
        LEFT JOIN public.staff_salary_profiles p ON p.user_id = u.id
        WHERE {where_sql}
    """
    return db._execute_query(count_sql, tuple(params) if params else None, fetch=True, commit=False)


def staff_index_select_invoice_companies():
    return db._execute_query(
        "SELECT id, name, tax_code, address FROM public.invoice_companies ORDER BY name",
        fetchall=True, commit=False,
    )


def list_staff(company_id, department, search_term, private_ids, limit, offset):
    where_sql, params = _staff_filter(company_id, department, search_term, private_ids)
    query_sql = f"""
        SELECT u.id, u.username, u.full_name, u.gender, u.nickname, u.phone, u.email,
               u.date_of_birth, u.id_number, u.company_ids, u.bank_account, u.bank_name, u.note,
               u.created_at, r.role_name as role,
               p.job_title, p.department,
               COALESCE(p.base_salary, 0) as base_salary,
               COALESCE(p.responsibility_allowance, 0) as responsibility_allowance,
               COALESCE(p.meal_allowance, 0) as meal_allowance,
               COALESCE(p.phone_allowance, 0) as phone_allowance,
               COALESCE(p.insurance_salary, 0) as insurance_salary,
               p.updated_at as profile_updated_at,
               p.user_id IS NOT NULL as has_salary_profile
        FROM public.users u
        LEFT JOIN public.roles r ON u.role_id = r.id
        LEFT JOIN public.staff_salary_profiles p ON p.user_id = u.id
        WHERE {where_sql}
        ORDER BY CASE WHEN lower(COALESCE(p.job_title, '')) LIKE '%%giám đốc%%' THEN 0 ELSE 1 END,
                 COALESCE(u.full_name, u.username) ASC
        LIMIT %s OFFSET %s
    """
    return db._execute_query(query_sql, tuple(list(params) + [limit, offset]), fetchall=True, commit=False)


def staff_index_select_staff_salary_profiles():
    return db._execute_query(
        "SELECT DISTINCT TRIM(department) as dept FROM public.staff_salary_profiles WHERE department IS NOT NULL AND TRIM(department) != '' ORDER BY dept",
        fetchall=True, commit=False,
    )


def staff_index_select_users():
    return db._execute_query("SELECT id, phone, id_number FROM public.users", fetchall=True, commit=False)


def staff_index_select_staff_contracts(uids):
    return db._execute_query(
        "SELECT * FROM public.staff_contracts WHERE user_id = ANY(%s) ORDER BY end_date DESC NULLS LAST",
        (uids,), fetchall=True, commit=False,
    )


def staff_index_select_staff_licenses(uids):
    return db._execute_query(
        "SELECT * FROM public.staff_licenses WHERE user_id = ANY(%s) ORDER BY expiry_date ASC NULLS LAST",
        (uids,), fetchall=True, commit=False,
    )


def get_staff_json_run_sql(user_id):
    query = """
        SELECT u.id, u.username, u.full_name, u.gender, u.nickname, u.phone, u.email,
               u.date_of_birth, u.id_number, u.company_ids, u.bank_account, u.bank_name, u.note,
               p.job_title, p.department,
               COALESCE(p.base_salary, 0) as base_salary,
               COALESCE(p.responsibility_allowance, 0) as responsibility_allowance,
               COALESCE(p.meal_allowance, 0) as meal_allowance,
               COALESCE(p.phone_allowance, 0) as phone_allowance,
               COALESCE(p.insurance_salary, 0) as insurance_salary
        FROM public.users u
        LEFT JOIN public.staff_salary_profiles p ON p.user_id = u.id
        WHERE u.id = %s
    """
    return db._execute_query(query, (user_id,), fetch=True, commit=False)


def create_staff_run_sql(username, role_id, full_name, gender, hashed_pwd, nickname, dob, company_ids, bank_name, note, phone, email, id_number, bank_account):
    insert_user_sql = """
        INSERT INTO public.users (
            username, hashed_password, role_id, full_name, gender, nickname,
            phone, email, date_of_birth, id_number, company_ids, bank_account, bank_name, note
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        ) RETURNING id
    """
    return db.execute_query(
        insert_user_sql,
        (
            username, encrypt_field(hashed_pwd), role_id,
            full_name, gender, clean_nickname(nickname),
            encrypt_field(phone.strip() or None), encrypt_field(email.strip() or None), encrypt_field(dob),
            encrypt_field(id_number.strip() or None), company_ids or None,
            encrypt_field(bank_account.strip() or None), bank_name.strip() or None, note.strip() or None
        ),
        commit=True, return_id=True,
    )


def create_staff_select_users(username):
    return db._execute_query("SELECT 1 FROM public.users WHERE username = %s", (username,), fetch=True, commit=False)


def update_staff_select_users(user_id):
    return db._execute_query("SELECT id FROM public.users WHERE id = %s", (user_id,), fetch=True, commit=False)


def update_staff_users_with_password(full_name, gender, user_id, nickname, dob, company_ids, hashed_pwd, bank_name, note, phone, email, id_number, bank_account):
    update_user_sql = """
        UPDATE public.users SET
            full_name = %s, gender = %s, nickname = %s, phone = %s, email = %s,
            date_of_birth = %s, id_number = %s, company_ids = %s,
            bank_account = %s, bank_name = %s, note = %s, hashed_password = %s,
            updated_at = now()
        WHERE id = %s
    """
    return db._execute_query(
        update_user_sql,
        (
            full_name, gender, clean_nickname(nickname), encrypt_field(phone.strip() or None),
            encrypt_field(email.strip() or None), encrypt_field(dob), encrypt_field(id_number.strip() or None),
            company_ids or None, encrypt_field(bank_account.strip() or None),
            bank_name.strip() or None, note.strip() or None, encrypt_field(hashed_pwd),
            user_id
        ),
        commit=True,
    )


def update_staff_users(full_name, gender, user_id, nickname, dob, company_ids, bank_name, note, phone, email, id_number, bank_account):
    update_user_sql = """
        UPDATE public.users SET
            full_name = %s, gender = %s, nickname = %s, phone = %s, email = %s,
            date_of_birth = %s, id_number = %s, company_ids = %s,
            bank_account = %s, bank_name = %s, note = %s,
            updated_at = now()
        WHERE id = %s
    """
    return db._execute_query(
        update_user_sql,
        (
            full_name, gender, clean_nickname(nickname), encrypt_field(phone.strip() or None),
            encrypt_field(email.strip() or None), encrypt_field(dob), encrypt_field(id_number.strip() or None),
            company_ids or None, encrypt_field(bank_account.strip() or None),
            bank_name.strip() or None, note.strip() or None,
            user_id
        ),
        commit=True,
    )


def delete_staff_select_users(user_id):
    return db._execute_query("SELECT id, username, full_name FROM public.users WHERE id = %s", (user_id,), fetch=True, commit=False)


def delete_staff_delete_staff_payroll_months(user_id):
    return db._execute_query("DELETE FROM public.staff_payroll_months WHERE user_id = %s", (user_id,), commit=True)


def delete_staff_delete_staff_salary_profiles(user_id):
    return db._execute_query("DELETE FROM public.staff_salary_profiles WHERE user_id = %s", (user_id,), commit=True)


def delete_staff_delete_users(user_id):
    return db._execute_query("DELETE FROM public.users WHERE id = %s", (user_id,), commit=True)


def delete_staff_select_invoice_companies(user_id):
    return db._execute_query(
        "SELECT id, name FROM public.invoice_companies WHERE director_user_id = %s",
        (user_id,), fetchall=True, commit=False,
    )


def get_staff_records_select_staff_contracts(user_id):
    return db._execute_query(
        "SELECT * FROM public.staff_contracts WHERE user_id = %s ORDER BY start_date DESC NULLS LAST",
        (user_id,), fetchall=True, commit=False,
    )


def get_staff_records_select_staff_licenses(user_id):
    return db._execute_query(
        "SELECT * FROM public.staff_licenses WHERE user_id = %s ORDER BY expiry_date ASC NULLS LAST",
        (user_id,), fetchall=True, commit=False,
    )


def get_staff_records_select_staff_work_history(user_id):
    return db._execute_query(
        "SELECT * FROM public.staff_work_history WHERE user_id = %s ORDER BY event_date DESC",
        (user_id,), fetchall=True, commit=False,
    )


def delete_staff_contract_delete_staff_contracts(contract_id):
    return db._execute_query("DELETE FROM public.staff_contracts WHERE id = %s", (contract_id,), commit=True)


def create_staff_license_insert_staff_licenses(user_id, license_type, issued_date, expiry_date, license_number, issued_by, note):
    return db._execute_query(
    """
        INSERT INTO public.staff_licenses (
            user_id, license_type, license_number, issued_date, expiry_date, issued_by, note
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
    (
        user_id, license_type, license_number.strip() or None,
        issued_date or None, expiry_date or None,
        issued_by.strip() or None, note.strip() or None
    ),
    commit=True,
    )


def delete_staff_license_delete_staff_licenses(license_id):
    return db._execute_query("DELETE FROM public.staff_licenses WHERE id = %s", (license_id,), commit=True)


def create_staff_work_history_insert_staff_work_history(user_id, event_type, event_date, user, old_value, new_value, decision_number, note):
    return db._execute_query(
        """
        INSERT INTO public.staff_work_history (
            user_id, event_type, event_date, old_value, new_value, decision_number, note, created_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id, event_type, event_date, old_value.strip() or None,
            new_value.strip() or None, decision_number.strip() or None,
            note.strip() or None, user.get("id")
        ),
        commit=True,
    )


def delete_staff_work_history_delete_staff_work_history(hist_id):
    return db._execute_query("DELETE FROM public.staff_work_history WHERE id = %s", (hist_id,), commit=True)


def create_staff_insert_staff_salary_profiles(user_id, job_title, department, base_salary, insurance_salary,
                                              responsibility_allowance, meal_allowance, phone_allowance):
    insert_profile_sql = """
        INSERT INTO public.staff_salary_profiles (
            user_id, job_title, department, base_salary, insurance_salary,
            responsibility_allowance, meal_allowance, phone_allowance, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, now()
        )
    """
    return db._execute_query(
        insert_profile_sql,
        (user_id, job_title, department, base_salary, insurance_salary,
         responsibility_allowance, meal_allowance, phone_allowance),
        commit=True,
    )


def update_staff_upsert_staff_salary_profiles(user_id, job_title, department, base_salary, insurance_salary,
                                              responsibility_allowance, meal_allowance, phone_allowance):
    upsert_profile_sql = """
        INSERT INTO public.staff_salary_profiles (
            user_id, job_title, department, base_salary, insurance_salary,
            responsibility_allowance, meal_allowance, phone_allowance, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, now()
        )
        ON CONFLICT (user_id) DO UPDATE SET
            job_title = EXCLUDED.job_title,
            department = EXCLUDED.department,
            base_salary = EXCLUDED.base_salary,
            insurance_salary = EXCLUDED.insurance_salary,
            responsibility_allowance = EXCLUDED.responsibility_allowance,
            meal_allowance = EXCLUDED.meal_allowance,
            phone_allowance = EXCLUDED.phone_allowance,
            updated_at = now()
    """
    return db._execute_query(
        upsert_profile_sql,
        (user_id, job_title, department, base_salary, insurance_salary,
         responsibility_allowance, meal_allowance, phone_allowance),
        commit=True,
    )


def create_staff_contract_insert_staff_contracts(user_id, contract_number, contract_type, sign_date, start_date, end_date,
                                                 salary_rate, insurance_rate, note):
    return db._execute_query(
        """
        INSERT INTO public.staff_contracts (
            user_id, contract_number, contract_type, sign_date, start_date,
            end_date, salary_rate, insurance_rate, note, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
        """,
        (user_id, contract_number, contract_type, sign_date, start_date, end_date, salary_rate, insurance_rate, note),
        commit=True,
    )
