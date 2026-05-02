from backend.core.database import DatabaseManager
import os

db = DatabaseManager()
try:
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'stranger_logs';
            """)
            columns = cursor.fetchall()
            print("Columns in stranger_logs:")
            for col in columns:
                print(f" - {col[0]} ({col[1]})")
except Exception as e:
    print(f"Error checking schema: {e}")
