from backend.services.database import db


def get_assigned_companies(user_id: int, user_role: str = "") -> list[dict]:
    if not user_id:
        return []
    row = db.select("users", columns="company_ids", conditions={"id": user_id}, fetchall=False)
    cids = (row.get("company_ids") if row else None) or []
    if cids:
        cids_tuple = tuple(int(x) for x in cids if str(x).isdigit())
        if cids_tuple:
            if len(cids_tuple) == 1:
                return db._execute_query(
                    "SELECT id, name, tax_code, address FROM public.invoice_companies WHERE id = %s ORDER BY name",
                    (cids_tuple[0],), fetchall=True, commit=False
                ) or []
            return db._execute_query(
                "SELECT id, name, tax_code, address FROM public.invoice_companies WHERE id IN %s ORDER BY name",
                (cids_tuple,), fetchall=True, commit=False
            ) or []
    if user_role == "admin":
        return db._execute_query(
            "SELECT id, name, tax_code, address FROM public.invoice_companies ORDER BY name",
            fetchall=True, commit=False
        ) or []
    return []


def get_company_by_id(company_id: int) -> dict | None:
    return db._execute_query(
        "SELECT id, name, tax_code, address, email FROM public.invoice_companies WHERE id = %s",
        (company_id,), fetch=True, commit=False
    )


def get_all_companies() -> list[dict]:
    return db._execute_query(
        "SELECT id, name, tax_code, address, email FROM public.invoice_companies ORDER BY name",
        fetchall=True, commit=False
    ) or []
