from backend.services.database import db


def get_role_permissions(role: str) -> str:
    if not role:
        return ""
    row = db._execute_query(
        "SELECT allowed_menus FROM roles WHERE LOWER(role_name) = LOWER(%s) LIMIT 1",
        (role,), fetch=True, commit=False
    )
    return row["allowed_menus"] if row else ""


def get_all_roles() -> list[dict]:
    return db._execute_query("SELECT id, role_name, allowed_menus FROM roles ORDER BY id", fetchall=True) or []
