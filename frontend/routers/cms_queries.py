"""Truy vấn cơ sở dữ liệu của frontend.routers.cms_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def count_projects_select_projects():
    return db._execute_query("SELECT COUNT(*) AS total FROM projects", fetch=True, commit=False)


def count_news_select_news():
    return db._execute_query("SELECT COUNT(*) AS total FROM news", fetch=True, commit=False)


def get_news_by_slug_select_news(slug):
    return db._execute_query(
        "SELECT id, slug, title, category, summary, content, image, publish_date FROM news WHERE slug = %s AND is_published IS DISTINCT FROM false",
        (slug,), fetch=True, commit=False
    )


def get_news_by_id_select_news(news_id):
    return db._execute_query(
        "SELECT id, slug, title, category, summary, content, image, publish_date FROM news WHERE id = %s AND is_published IS DISTINCT FROM false",
        (news_id,), fetch=True, commit=False
    )


def get_landing_settings_select_app_settings():
    return db._execute_query(
        "SELECT key, value FROM app_settings WHERE key LIKE 'LANDING_%' OR key IN ('GTM_ID', 'FB_PIXEL_ID')",
        fetchall=True,
        commit=False,
    )


def add_project_insert_projects(name, location, area, height, bays, load_capacity, image):
    return db._execute_query(
        "INSERT INTO projects (name, location, area, height, bays, load_capacity, image) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (name, location, area, height, bays, load_capacity, image), commit=True
    )


def news_edit_page_select_news(news_id):
    return db._execute_query("SELECT * FROM news WHERE id=%s", (news_id,), fetch=True)


def news_save_update_news(title, category, summary, content, image, published, news_id_value):
    return db._execute_query(
        "UPDATE news SET title=%s, category=%s, summary=%s, content=%s, image=%s, is_published=%s WHERE id=%s",
        (title, category, summary, content, image, published, news_id_value), commit=True
    )


def news_save_insert_news(title, category, summary, content, image, published):
    return db._execute_query(
        "INSERT INTO news (title, category, summary, content, image, is_published) VALUES (%s, %s, %s, %s, %s, %s)",
        (title, category, summary, content, image, published), commit=True
    )


def delete_project_delete_projects(project_id):
    return db.delete("projects", {"id": project_id})


def delete_news_delete_news(news_id):
    return db.delete("news", {"id": news_id})


def update_project_update_projects(name, location, area, height, bays, load_capacity, image, project_id):
    return db._execute_query(
        "UPDATE projects SET name=%s, location=%s, area=%s, height=%s, bays=%s, load_capacity=%s, image=%s WHERE id=%s",
        (name, location, area, height, bays, load_capacity, image, project_id), commit=True
    )


def get_projects(limit: int = 20, offset: int = 0) -> list:
    query = """
        SELECT id, name, location, area, height, bays, load_capacity, image, description
        FROM projects
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """
    return db._execute_query(query, (limit, offset), fetchall=True, commit=False) or []


def get_news(limit: int = 20, offset: int = 0) -> list:
    query = """
        SELECT id, slug, title, category, summary, image, publish_date
        FROM news
        ORDER BY publish_date DESC
        LIMIT %s OFFSET %s
    """
    rows = db._execute_query(query, (limit, offset), fetchall=True, commit=False) or []
    for row in rows:
        if row.get("publish_date") and not isinstance(row["publish_date"], str):
            row["publish_date"] = row["publish_date"].isoformat()
    return rows
