"""Truy vấn cơ sở dữ liệu của frontend.modules.payroll.controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def validate_department_select_enterprise_departments(clean):
    return db._execute_query("SELECT 1 FROM public.enterprise_departments WHERE LOWER(TRIM(name)) = %s", (clean,), fetch=True, commit=False)
