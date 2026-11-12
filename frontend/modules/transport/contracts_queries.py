"""Truy vấn cơ sở dữ liệu của frontend.modules.transport.contracts_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db
from frontend.context.company_context import get_current_company_id


def get_contract_categories_select_cargo_catalog_items():
    return db._execute_query(
        "SELECT id, name FROM public.cargo_catalog_items WHERE kind = 'contract_category' AND company_id = %s ORDER BY id",
        (get_current_company_id(),), fetchall=True, commit=False,
    )


def export_contract_select_cargo_trips(trip_id):
    return db._execute_query(
        "SELECT * FROM cargo_trips WHERE id = %s", (trip_id,), fetch=True, commit=False,
    )
