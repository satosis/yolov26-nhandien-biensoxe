import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def drop_cargo_type():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not found")
        return
    
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    
    try:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        
        col = "cargo_type"
        print(f"Checking for column '{col}' in 'cargo_trips'...")
        cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name='cargo_trips' AND column_name='{col}';")
        if cur.fetchone():
            print(f"Dropping column '{col}' from 'cargo_trips'...")
            cur.execute(f"ALTER TABLE cargo_trips DROP COLUMN {col};")
            conn.commit()
            print(f"Column '{col}' dropped successfully.")
        else:
            print(f"Column '{col}' does not exist in 'cargo_trips'.")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    drop_cargo_type()
