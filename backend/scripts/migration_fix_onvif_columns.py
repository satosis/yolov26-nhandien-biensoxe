
import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
dsn = os.environ.get("DATABASE_URL")
if not dsn:
    # Try alternate path for .env if not found
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    dsn = os.environ.get("DATABASE_URL")

if not dsn:
    print("FATAL: DATABASE_URL not found in environment.")
    exit(1)

# Psycopg2 requires postgresql:// instead of postgresql+asyncpg://
dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

def run_migration():
    try:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        
        print("Starting database migration...")
        
        # 1. Add column to cameras table
        print("Checking 'cameras' table for 'onvif_rtsp_detected'...")
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='cameras' AND column_name='onvif_rtsp_detected'")
        if not cur.fetchone():
            print("Adding 'onvif_rtsp_detected' to 'cameras'...")
            cur.execute("ALTER TABLE cameras ADD COLUMN onvif_rtsp_detected TEXT")
        else:
            print("'onvif_rtsp_detected' already exists in 'cameras'.")

        # 2. Add columns to cameras_asset table
        print("Checking 'cameras_asset' table for 'onvif_port'...")
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='cameras_asset' AND column_name='onvif_port'")
        if not cur.fetchone():
            print("Adding 'onvif_port' to 'cameras_asset'...")
            cur.execute("ALTER TABLE cameras_asset ADD COLUMN onvif_port INTEGER")
        else:
            print("'onvif_port' already exists in 'cameras_asset'.")

        print("Checking 'cameras_asset' table for 'onvif_profiles'...")
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='cameras_asset' AND column_name='onvif_profiles'")
        if not cur.fetchone():
            print("Adding 'onvif_profiles' to 'cameras_asset'...")
            cur.execute("ALTER TABLE cameras_asset ADD COLUMN onvif_profiles JSONB")
        else:
            print("'onvif_profiles' already exists in 'cameras_asset'.")

        conn.commit()
        print("Migration completed successfully.")
        
    except Exception as e:
        print(f"Migration failed: {e}")
        if 'conn' in locals():
            conn.rollback()
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    run_migration()
