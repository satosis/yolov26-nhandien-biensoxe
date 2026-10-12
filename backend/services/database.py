import os
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from dotenv import load_dotenv

_env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
load_dotenv(_env_file if os.path.exists(_env_file) else None)


class DatabaseManager:
    def __init__(self, max_pool_size=15):
        self._pool = pool.ThreadedConnectionPool(1, max_pool_size, os.environ.get("DATABASE_URL", ""))

    @contextmanager
    def _get_connection(self):
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            try:
                conn.rollback()
            finally:
                self._pool.putconn(conn)

    def _execute_query(self, query, params=None, fetch=False, fetchall=False, commit=True, return_id=False):
        words = query.lstrip().split(None, 1)
        raw_statement = words[0].upper() if words else ""
        statement = raw_statement if raw_statement in {"SELECT", "INSERT", "UPDATE", "DELETE", "WITH"} else "OTHER"
        with self._get_connection() as conn:
            cursor_factory = RealDictCursor if fetch or fetchall else None
            with conn.cursor(cursor_factory=cursor_factory) as cursor:
                cursor.execute(query, params)
                result = cursor.rowcount > 0 if statement in {"INSERT", "UPDATE", "DELETE"} else True
                if fetch:
                    row = cursor.fetchone()
                    result = dict(row) if row is not None else None
                elif fetchall:
                    result = [dict(row) for row in cursor.fetchall()]
                elif return_id:
                    row = cursor.fetchone()
                    result = row[0] if row else None
                if commit:
                    conn.commit()
        return result

    execute_query = _execute_query

    def insert(self, table_name, data: dict, returning=None):
        columns = ', '.join(data.keys())
        values_template = ', '.join(['%s'] * len(data))
        query = f"INSERT INTO {table_name} ({columns}) VALUES ({values_template})"
        if returning:
            query += f" RETURNING {returning}"
        return self._execute_query(query, tuple(data.values()), commit=True, return_id=bool(returning))

    def update(self, table_name, data: dict, conditions: dict):
        if not data:
            raise ValueError("Database update requires values")
        if not conditions:
            raise ValueError("Database update requires conditions")
        set_clause = ', '.join([f"{k} = %s" for k in data.keys()])
        where_clause = ' AND '.join([f"{k} = %s" for k in conditions.keys()])
        query = f"UPDATE {table_name} SET {set_clause} WHERE {where_clause}"
        params = tuple(data.values()) + tuple(conditions.values())
        return self._execute_query(query, params, commit=True)

    def select(self, table_name, columns='*', conditions=None, order_by=None, limit=None, fetchall=True):
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
        if not conditions:
            raise ValueError("Database delete requires conditions")
        where_clause = ' AND '.join([f"{k} = %s" for k in conditions.keys()])
        query = f"DELETE FROM {table_name} WHERE {where_clause}"
        return self._execute_query(query, tuple(conditions.values()), commit=True)


# Global database manager instance
db = DatabaseManager()


def get_db() -> DatabaseManager:
    return db


