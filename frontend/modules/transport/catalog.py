"""Editable choices for cargo services and goods."""

from backend.services.database import db


KINDS = {"service": "Dịch vụ", "cargo_type": "Loại hàng hóa"}


def clean_catalog_name(value: str) -> str:
    name = " ".join(str(value or "").split())
    if not name:
        raise ValueError("Tên danh mục không được để trống.")
    if len(name) > 200:
        raise ValueError("Tên danh mục không được vượt quá 200 ký tự.")
    return name


def list_items(kind: str | None = None, company_id: int | None = None) -> list[dict]:
    if kind is not None and kind not in KINDS:
        raise ValueError("Danh mục không hợp lệ.")
    where_parts = []
    params = []
    if kind:
        where_parts.append("kind = %s")
        params.append(kind)
    if company_id is not None:
        where_parts.append("(company_id = %s OR company_id IS NULL)")
        params.append(company_id)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    return db._execute_query(
        f"SELECT id, kind, name FROM public.cargo_catalog_items {where_sql} ORDER BY kind, name, id",
        tuple(params) if params else None,
        fetchall=True, commit=False,
    ) or []


def count_items(kind: str, company_id: int | None = None) -> int:
    if kind not in KINDS:
        raise ValueError("Danh mục không hợp lệ.")
    row = db._execute_query(
        """SELECT count(*) AS total FROM public.cargo_catalog_items
           WHERE kind = %s AND (%s IS NULL OR company_id = %s OR company_id IS NULL)""",
        (kind, company_id, company_id), fetch=True, commit=False,
    )
    return int(row["total"] if row else 0)


def list_items_page(kind: str, company_id: int | None = None, *, limit: int = 20, offset: int = 0) -> list[dict]:
    if kind not in KINDS:
        raise ValueError("Danh mục không hợp lệ.")
    return db._execute_query(
        """SELECT id, kind, name FROM public.cargo_catalog_items
           WHERE kind = %s AND (%s IS NULL OR company_id = %s OR company_id IS NULL)
           ORDER BY name, id LIMIT %s OFFSET %s""",
        (kind, company_id, company_id, limit, offset), fetchall=True, commit=False,
    ) or []


def get_item(item_id: int) -> dict | None:
    return db._execute_query(
        "SELECT id, kind, name FROM public.cargo_catalog_items WHERE id = %s",
        (item_id,), fetch=True, commit=False,
    )


def save_item(kind: str, name: str, item_id: int | None = None, company_id: int | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError("Danh mục không hợp lệ.")
    name = clean_catalog_name(name)
    if kind == "service":
        cargo_suffix = db._execute_query(
            """SELECT id FROM public.cargo_catalog_items
               WHERE kind = 'cargo_type'
                 AND (%s IS NULL OR company_id = %s OR company_id IS NULL)
                 AND right(lower(%s), char_length(name) + 1) = ' ' || lower(name)
               LIMIT 1""",
            (company_id, company_id, name), fetch=True, commit=False,
        )
        if cargo_suffix:
            raise ValueError("Tên dịch vụ không kèm loại hàng hóa; hãy chọn loại hàng hóa ở trường riêng.")
    duplicate = db._execute_query(
        """SELECT id FROM public.cargo_catalog_items
           WHERE kind = %s AND LOWER(name) = LOWER(%s) AND id <> COALESCE(%s, -1)
             AND (company_id = %s OR company_id IS NULL OR %s IS NULL)""",
        (kind, name, item_id, company_id, company_id), fetch=True, commit=False,
    )
    if duplicate:
        raise ValueError("Tên này đã có trong danh mục.")
    if item_id is None:
        return db._execute_query(
            """INSERT INTO public.cargo_catalog_items (kind, name, company_id) VALUES (%s, %s, %s)
               RETURNING id, kind, name""",
            (kind, name, company_id), fetch=True, commit=True,
        )
    saved = db._execute_query(
        """UPDATE public.cargo_catalog_items SET name = %s, updated_at = NOW()
           WHERE id = %s AND kind = %s RETURNING id, kind, name""",
        (name, item_id, kind), fetch=True, commit=True,
    )
    if not saved:
        raise ValueError("Không tìm thấy mục danh mục.")
    return saved


def delete_item(kind: str, item_id: int) -> bool:
    if kind not in KINDS:
        raise ValueError("Danh mục không hợp lệ.")
    return bool(db._execute_query(
        "DELETE FROM public.cargo_catalog_items WHERE id = %s AND kind = %s RETURNING id",
        (item_id, kind), fetch=True, commit=True,
    ))
