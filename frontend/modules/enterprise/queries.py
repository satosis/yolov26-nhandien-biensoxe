"""Truy vấn cơ sở dữ liệu của frontend.modules.enterprise.controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db
from backend.services.crypto_service import encrypt_field


def enterprise_page_select_users():
    return db._execute_query(
        "SELECT id, username, full_name, email, phone FROM public.users ORDER BY COALESCE(full_name, username) ASC",
        fetchall=True, commit=False,
    )


def enterprise_page_select_users_2(company):
    return db._execute_query(
        "SELECT COUNT(*) as cnt FROM public.users WHERE %s = ANY(company_ids)",
        (company["id"],), fetch=True, commit=False,
    )


def departments_page_select_enterprise_departments(cid):
    return db._execute_query(
        "SELECT COUNT(*) AS total FROM public.enterprise_departments WHERE company_id = %s",
        (cid,), fetch=True, commit=False,
    )


def departments_page_select_staff_salary_profiles(cid, page_data):
    return db._execute_query(
        """
        SELECT d.*, 
               u.full_name as manager_name, u.username as manager_username,
               (SELECT COUNT(*) FROM public.staff_salary_profiles s WHERE s.department = d.name) as member_count
        FROM public.enterprise_departments d
        LEFT JOIN public.users u ON d.manager_user_id = u.id
        WHERE d.company_id = %s
        ORDER BY d.name ASC
        LIMIT %s OFFSET %s
        """,
        (cid, page_data["per_page"], (page_data["page"] - 1) * page_data["per_page"]), fetchall=True, commit=False,
    )


def departments_page_select_users():
    return db._execute_query(
        "SELECT id, username, full_name FROM public.users ORDER BY COALESCE(full_name, username) ASC",
        fetchall=True, commit=False,
    )


def department_create_insert_enterprise_departments(cid, name, mgr_id, code, description):
    return db._execute_query(
        """
        INSERT INTO public.enterprise_departments (company_id, code, name, manager_user_id, description)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (cid, code.strip() or None, name, mgr_id, description.strip() or None),
        commit=True,
    )


def department_edit_update_enterprise_departments(name, mgr_id, dept_id, code, description):
    return db._execute_query(
        """
        UPDATE public.enterprise_departments
        SET name = %s, code = %s, manager_user_id = %s, description = %s
        WHERE id = %s
        """,
        (name, code.strip() or None, mgr_id, description.strip() or None, dept_id),
        commit=True,
    )


def department_delete_delete_enterprise_departments(dept_id):
    return db._execute_query("DELETE FROM public.enterprise_departments WHERE id = %s", (dept_id,), commit=True)


def attendance_page_select_users(cid):
    return db._execute_query(
        """SELECT COUNT(*) AS total FROM public.users u
           WHERE %s = ANY(u.company_ids)
             AND u.username IS DISTINCT FROM 'admin'""",
        (cid,), fetch=True, commit=False,
    )


def attendance_page_select_users_2(month_date, cid, attendance_pagination):
    return db._execute_query(
        """
        SELECT u.id, u.username, u.full_name, u.phone,
               sp.job_title, sp.department,
               att.standard_days, att.actual_days, att.paid_leave_days, 
               att.unpaid_leave_days, att.overtime_hours, att.note as att_note
        FROM public.users u
        LEFT JOIN public.staff_salary_profiles sp ON u.id = sp.user_id
        LEFT JOIN public.staff_attendance att ON u.id = att.user_id AND att.month = %s
        WHERE %s = ANY(u.company_ids)
          AND u.username IS DISTINCT FROM 'admin'
        ORDER BY COALESCE(u.full_name, u.username) ASC
        LIMIT %s OFFSET %s
        """,
        (month_date, cid, attendance_pagination["per_page"],
         (attendance_pagination["page"] - 1) * attendance_pagination["per_page"]),
        fetchall=True, commit=False,
    )


def attendance_page_select_staff_leave_requests(cid):
    return db._execute_query(
        "SELECT COUNT(*) AS total FROM public.staff_leave_requests WHERE company_id = %s",
        (cid,), fetch=True, commit=False,
    )


def attendance_page_select_staff_leave_requests_2(cid, leave_pagination):
    return db._execute_query(
        """
        SELECT r.*, u.full_name, u.username,
               approver.full_name as approver_name
        FROM public.staff_leave_requests r
        JOIN public.users u ON r.user_id = u.id
        LEFT JOIN public.users approver ON r.approved_by = approver.id
        WHERE r.company_id = %s
        ORDER BY r.created_at DESC
        LIMIT %s OFFSET %s
        """,
        (cid, leave_pagination["per_page"],
         (leave_pagination["page"] - 1) * leave_pagination["per_page"]),
        fetchall=True, commit=False,
    )


def attendance_page_select_users_3():
    return db._execute_query(
        "SELECT id, username, full_name FROM public.users WHERE username IS DISTINCT FROM 'admin' "
        "ORDER BY COALESCE(full_name, username) ASC",
        fetchall=True, commit=False,
    )


def attendance_save_row_insert_staff_attendance(user_id, cid, month_date, standard_days, actual_days, paid_leave_days, unpaid_leave_days, overtime_hours, note):
    return db._execute_query(
        """
        INSERT INTO public.staff_attendance (
            user_id, company_id, month, standard_days, actual_days, 
            paid_leave_days, unpaid_leave_days, overtime_hours, note, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (user_id, month) DO UPDATE SET
            standard_days = EXCLUDED.standard_days,
            actual_days = EXCLUDED.actual_days,
            paid_leave_days = EXCLUDED.paid_leave_days,
            unpaid_leave_days = EXCLUDED.unpaid_leave_days,
            overtime_hours = EXCLUDED.overtime_hours,
            note = EXCLUDED.note,
            updated_at = NOW()
        """,
        (user_id, cid, month_date, standard_days, actual_days, paid_leave_days, unpaid_leave_days, overtime_hours, note.strip() or None),
        commit=True,
    )


def attendance_save_row_insert_staff_payroll_months(user_id, month_date, actual_days, standard_days):
    return db._execute_query(
        """
        INSERT INTO public.staff_payroll_months (
            user_id, month, work_days, standard_days, updated_at
        ) VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (user_id, month) DO UPDATE SET
            work_days = EXCLUDED.work_days,
            standard_days = EXCLUDED.standard_days,
            updated_at = NOW()
        """,
        (user_id, month_date, actual_days, standard_days),
        commit=True,
    )


def attendance_quick_fill_select_users(cid):
    return db._execute_query(
        "SELECT id FROM public.users WHERE (%s = ANY(company_ids) OR company_ids IS NULL) "
        "AND username IS DISTINCT FROM 'admin'",
        (cid,), fetchall=True, commit=False,
    )


def attendance_quick_fill_insert_staff_attendance(cid, month_date, standard_days, s):
    return db._execute_query(
        """
            INSERT INTO public.staff_attendance (
                user_id, company_id, month, standard_days, actual_days, 
                paid_leave_days, unpaid_leave_days, overtime_hours, updated_at
            ) VALUES (%s, %s, %s, %s, %s, 0, 0, 0, NOW())
            ON CONFLICT (user_id, month) DO UPDATE SET
                standard_days = EXCLUDED.standard_days,
                actual_days = CASE WHEN public.staff_attendance.actual_days = 0 THEN EXCLUDED.standard_days ELSE public.staff_attendance.actual_days END,
                updated_at = NOW()
            """,
        (s["id"], cid, month_date, standard_days, standard_days),
        commit=True,
    )


def leave_request_create_insert_staff_leave_requests(cid, user_id, leave_type, start_date, end_date, total_days, reason):
    return db._execute_query(
        """
        INSERT INTO public.staff_leave_requests (company_id, user_id, leave_type, start_date, end_date, total_days, reason, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
        """,
        (cid, user_id, leave_type, start_date, end_date, total_days, reason.strip() or None),
        commit=True,
    )


def leave_request_update_status_update_staff_leave_requests(status, approver_id, req_id):
    return db._execute_query(
        """
        UPDATE public.staff_leave_requests
        SET status = %s, approved_by = %s, approved_at = NOW()
        WHERE id = %s
        """,
        (status, approver_id, req_id),
        commit=True,
    )


def leave_request_delete_delete_staff_leave_requests(req_id):
    return db._execute_query("DELETE FROM public.staff_leave_requests WHERE id = %s", (req_id,), commit=True)


def advances_page_select_enterprise_internal_advances(params, where):
    return db._execute_query(
        f"""SELECT COUNT(*) AS total, COALESCE(SUM(a.amount), 0) AS total_amount,
                   COALESCE(SUM(a.settled_amount), 0) AS total_settled
            FROM public.enterprise_internal_advances a {where}""",
        tuple(params), fetch=True, commit=False,
    )


def advances_page_select_enterprise_internal_advances_2(where, params, page_data):
    return db._execute_query(
        f"""SELECT a.*, u.full_name, u.username, u.bank_account as u_bank_acc,
                   u.bank_name as u_bank_name, creator.full_name as creator_name
            FROM public.enterprise_internal_advances a
            JOIN public.users u ON a.user_id = u.id
            LEFT JOIN public.users creator ON a.created_by = creator.id
            {where}
            ORDER BY a.advance_date DESC, a.id DESC LIMIT %s OFFSET %s""",
        tuple([*params, page_data["per_page"], (page_data["page"] - 1) * page_data["per_page"]]),
        fetchall=True, commit=False,
    )


def advances_page_select_users():
    return db._execute_query(
        "SELECT id, username, full_name, bank_account, bank_name FROM public.users ORDER BY COALESCE(full_name, username) ASC",
        fetchall=True, commit=False,
    )


def advance_create_select_users(user_id):
    return db._execute_query("SELECT id FROM public.users WHERE id = %s", (user_id,), fetch=True, commit=False)


def advance_create_insert_enterprise_internal_advances(cid, user_id, clean_adv_type, num_amt, adv_date, user, reason, bank_name, bank_account):
    return db._execute_query(
        """
        INSERT INTO public.enterprise_internal_advances (
            company_id, user_id, advance_type, amount, advance_date, reason, 
            status, payment_method, bank_account, bank_name, created_by
        ) VALUES (%s, %s, %s, %s, %s, %s, 'pending', 'transfer', %s, %s, %s)
        """,
        (cid, user_id, clean_adv_type, num_amt, adv_date, reason.strip() or None, encrypt_field(bank_account.strip() or None), bank_name.strip() or None, user.get("id")),
        commit=True,
    )


def advance_settle_update_enterprise_internal_advances(num_settled, settled_date, adv_id):
    return db._execute_query(
        """
        UPDATE public.enterprise_internal_advances
        SET settled_amount = %s, settled_date = %s, 
            status = CASE WHEN %s >= amount THEN 'settled' ELSE 'paid' END
        WHERE id = %s
        """,
        (num_settled, settled_date, num_settled, adv_id),
        commit=True,
    )


def advance_update_status_update_enterprise_internal_advances(status, adv_id):
    return db._execute_query(
        "UPDATE public.enterprise_internal_advances SET status = %s WHERE id = %s",
        (status, adv_id), commit=True,
    )


def advance_delete_delete_enterprise_internal_advances(adv_id):
    return db._execute_query("DELETE FROM public.enterprise_internal_advances WHERE id = %s", (adv_id,), commit=True)


def asset_assignment_create_insert_enterprise_asset_assignments(cid, user_id, asset_type, v_id, handover_date, odo, asset_name, user, asset_code, condition_note):
    return db._execute_query(
        """
        INSERT INTO public.enterprise_asset_assignments (
            company_id, user_id, asset_type, vehicle_id, asset_name, asset_code,
            handover_date, initial_odo, status, condition_note, created_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'in_use', %s, %s)
        """,
        (cid, user_id, asset_type, v_id, asset_name.strip(), asset_code.strip() or None, handover_date, odo, condition_note.strip() or None, user.get("id")),
        commit=True,
    )


def asset_assignment_return_update_enterprise_asset_assignments(return_date, odo, assign_id, condition_note):
    return db._execute_query(
        """
        UPDATE public.enterprise_asset_assignments
        SET status = 'returned', return_date = %s, return_odo = %s,
            condition_note = CASE 
                WHEN condition_note IS NULL THEN %s 
                ELSE condition_note || E'\n[Thu hồi]: ' || %s 
            END
        WHERE id = %s
        """,
        (return_date, odo, condition_note.strip() or "Đã thu hồi", condition_note.strip() or "Đã thu hồi", assign_id),
        commit=True,
    )


def asset_assignment_delete_delete_enterprise_asset_assignments(assign_id):
    return db._execute_query("DELETE FROM public.enterprise_asset_assignments WHERE id = %s", (assign_id,), commit=True)


def enterprise_debts_page_select_company_debt_accounts(owner_company_id, month_start, month_end):
    return db._execute_query(
        """SELECT
             COALESCE((SELECT SUM(GREATEST(current_debt, 0))
                       FROM public.company_debt_accounts WHERE owner_company_id = %s), 0) AS total_debt_all,
             COALESCE((SELECT COUNT(*) FROM public.company_debt_accounts
                       WHERE owner_company_id = %s AND current_debt > 0), 0) AS debt_company_count,
             COALESCE((SELECT COUNT(DISTINCT company_id) FROM public.company_debt_accounts
                       WHERE owner_company_id = %s), 0) AS total_companies,
             COALESCE((SELECT SUM(amount) FROM public.company_debt_logs
                       WHERE owner_company_id = %s AND recorded_date >= %s AND recorded_date < %s AND amount > 0), 0) AS total_debt_added,
             COALESCE((SELECT SUM(ABS(amount)) FROM public.company_debt_logs
                       WHERE owner_company_id = %s AND recorded_date >= %s AND recorded_date < %s AND amount < 0), 0) AS total_debt_paid""",
        (owner_company_id, owner_company_id, owner_company_id,
         owner_company_id, month_start, month_end,
         owner_company_id, month_start, month_end),
        fetch=True, commit=False,
    )


def enterprise_debts_page_select_invoice_companies(where_sql, params, owner_company_id):
    return db._execute_query(
        f"""SELECT COUNT(*) AS total
            FROM public.invoice_companies c
            LEFT JOIN public.company_debt_accounts a
              ON a.company_id = c.id AND a.owner_company_id = %s
            {where_sql}""",
        tuple([owner_company_id] + params), fetch=True, commit=False,
    )


def enterprise_debts_page_select_company_debt_logs(where_sql, params, company_fetch_limit, company_fetch_offset, owner_company_id):
    return db._execute_query(
        f"""SELECT c.id, c.name, c.tax_code, c.phone, c.address,
                   COALESCE(a.current_debt, 0) AS current_debt,
                   (SELECT COUNT(*) FROM public.company_debt_logs l
                    WHERE l.owner_company_id = %s AND l.company_id = c.id) AS log_count,
                   (SELECT MAX(l.recorded_date) FROM public.company_debt_logs l
                    WHERE l.owner_company_id = %s AND l.company_id = c.id) AS last_log_date
            FROM public.invoice_companies c
            LEFT JOIN public.company_debt_accounts a
              ON a.company_id = c.id AND a.owner_company_id = %s
            {where_sql}
            ORDER BY COALESCE(a.current_debt, 0) DESC, c.name ASC
            LIMIT %s OFFSET %s""",
        tuple([owner_company_id, owner_company_id, owner_company_id] + params + [
            company_fetch_limit,
            company_fetch_offset,
        ]),
        fetchall=True, commit=False,
    )


def enterprise_debts_page_select_invoice_companies_2(owner_company_id):
    return db._execute_query(
        """SELECT c.id, c.name, c.tax_code, COALESCE(a.current_debt, 0) AS current_debt
           FROM public.invoice_companies c
           LEFT JOIN public.company_debt_accounts a
             ON a.company_id = c.id AND a.owner_company_id = %s
           ORDER BY c.name ASC""",
        (owner_company_id,), fetchall=True, commit=False,
    )


def enterprise_debts_page_select_company_debt_logs_2(log_params, log_where_sql):
    return db._execute_query(
        f"""SELECT COUNT(*) AS total
            FROM public.company_debt_logs l
            JOIN public.invoice_companies c ON c.id = l.company_id
            {log_where_sql}""",
        tuple(log_params), fetch=True, commit=False,
    )


def enterprise_debts_page_select_company_debt_logs_3(log_where_sql, log_params, log_pagination):
    return db._execute_query(
        f"""SELECT l.id, l.company_id, l.amount, l.debt_type, l.note,
                  l.recorded_date, l.reference_doc, l.created_at, l.created_by,
                  c.name AS company_name, c.tax_code AS company_tax_code,
                  COALESCE(u.full_name, u.username) AS creator_name
           FROM public.company_debt_logs l
           JOIN public.invoice_companies c ON c.id = l.company_id
           LEFT JOIN public.users u ON u.id = l.created_by
           {log_where_sql}
           ORDER BY l.recorded_date DESC, l.id DESC LIMIT %s OFFSET %s""",
        tuple(log_params + [log_pagination["per_page"],
              (log_pagination["page"] - 1) * log_pagination["per_page"]]),
        fetchall=True, commit=False,
    )


def enterprise_debt_pay_select_invoice_companies(company_id):
    return db.select("invoice_companies", columns="name", conditions={"id": company_id}, fetchall=False)


def personal_debt_customer_create_select_personal_debt_customers(company_id, code):
    return db._execute_query(
        "SELECT 1 FROM public.personal_debt_customers WHERE company_id = %s AND customer_code = %s",
        (company_id, code), fetch=True, commit=False
    )


def enterprise_sales_invoice_pdf_select_personal_sales(sale_id):
    return db._execute_query(
        "SELECT company_id FROM public.personal_sales WHERE id = %s",
        (sale_id,),
        fetch=True,
        commit=False,
    )
