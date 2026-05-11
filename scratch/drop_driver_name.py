import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def drop_column():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not found")
        return
    
    # Fix for async dsn if present
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    
    try:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        print("Checking for column 'driver_name' in 'cargo_trips'...")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='cargo_trips' AND column_name='driver_name';")
        if cur.fetchone():
            print("Dropping column 'driver_name' from 'cargo_trips'...")
            cur.execute("ALTER TABLE cargo_trips DROP COLUMN driver_name;")
            conn.commit()
            print("Column dropped successfully.")
        else:
            print("Column 'driver_name' does not exist in 'cargo_trips'.")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    drop_column()
