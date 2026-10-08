import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import logging
import threading
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
load_dotenv(_env_file if os.path.exists(_env_file) else None)


class DatabaseManager:
    _pool = None
    _pool_lock = threading.Lock()

    @staticmethod
    def _get_db_url() -> str:
        return os.environ.get("DATABASE_URL", "")

    def __init__(self, max_pool_size=None):
        self.max_pool_size = int(max_pool_size) if max_pool_size else 15
        if DatabaseManager._pool is None:
            self._init_pool(self.max_pool_size)

    @classmethod
    def _init_pool(cls, maxconn: int = 15):
        """Khởi tạo connection pool từ DATABASE_URL."""
        with cls._pool_lock:
            if cls._pool is None:
                cls._pool = pool.ThreadedConnectionPool(1, maxconn, cls._get_db_url())
                logger.info("Database connection pool initialized (maxconn=%d)", maxconn)
            return cls._pool

    @contextmanager
    def _get_connection(self):
        conn = DatabaseManager._pool.getconn()
        try:
            yield conn
        finally:
            DatabaseManager._pool.putconn(conn)

    def _execute_query(self, query, params=None, fetch=False, fetchall=False, commit=True, return_id=False):
        """Core method execute queries trực tiếp, lỗi báo từ nguồn."""
        with self._get_connection() as conn:
            cursor_factory = RealDictCursor if fetch or fetchall else None
            with conn.cursor(cursor_factory=cursor_factory) as cursor:
                cursor.execute(query, params)
                result = True
                if fetch:
                    result = cursor.fetchone()
                    if result and cursor_factory:
                        result = dict(result)
                elif fetchall:
                    result = cursor.fetchall()
                    if result and cursor_factory:
                        result = [dict(r) for r in result]
                elif return_id:
                    row = cursor.fetchone()
                    result = row[0] if row else None
                if commit:
                    conn.commit()
                else:
                    conn.rollback()  # Clear transaction state for read-only queries
                return result

    execute_query = _execute_query

    def insert(self, table_name, data: dict, returning=None):
        """Generic insert."""
        columns = ', '.join(data.keys())
        values_template = ', '.join(['%s'] * len(data))
        query = f"INSERT INTO {table_name} ({columns}) VALUES ({values_template})"
        if returning:
            query += f" RETURNING {returning}"
        return self._execute_query(query, tuple(data.values()), commit=True, return_id=bool(returning))

    def update(self, table_name, data: dict, conditions: dict):
        """Generic update."""
        if not data or not conditions:
            return False
        set_clause = ', '.join([f"{k} = %s" for k in data.keys()])
        where_clause = ' AND '.join([f"{k} = %s" for k in conditions.keys()])
        query = f"UPDATE {table_name} SET {set_clause} WHERE {where_clause}"
        params = tuple(data.values()) + tuple(conditions.values())
        return self._execute_query(query, params, commit=True) is not None

    def select(self, table_name, columns='*', conditions=None, order_by=None, limit=None, fetchall=True):
        """Generic select."""
        if isinstance(columns, list):
            columns = ', '.join(columns)
        query = f"SELECT {columns} FROM {table_name}"
        params = []
        if conditions:
            where_clause = ' AND '.join([f"{k} = %s" for k in conditions.keys()])
            query += f" WHERE {where_clause}"
            params = list(conditions.values())
        if order_by:
            query += f" ORDER BY {order_by}"
        if limit:
            query += " LIMIT %s"
            params.append(int(limit))
        return self._execute_query(query, tuple(params) if params else None, fetch=not fetchall, fetchall=fetchall, commit=False)

    def delete(self, table_name, conditions: dict):
        """Generic delete."""
        if not conditions:
            return False
        where_clause = ' AND '.join([f"{k} = %s" for k in conditions.keys()])
        query = f"DELETE FROM {table_name} WHERE {where_clause}"
        return self._execute_query(query, tuple(conditions.values()), commit=True) is not None
