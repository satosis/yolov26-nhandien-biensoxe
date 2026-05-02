from backend.core.database import DatabaseManager
import logging

logging.basicConfig(level=logging.INFO)
db = DatabaseManager()

try:
    # Check if table exists
    db._execute_query("SELECT 1 FROM video_recordings LIMIT 1")
    print("SUCCESS: video_recordings table exists.")
    
    # Check allowance
    from backend.core.database import DatabaseManager as DB
    print(f"Allowed tables: {DB._ALLOWED_TABLES}")
    if 'video_recordings' in DB._ALLOWED_TABLES:
        print("SUCCESS: video_recordings added to _ALLOWED_TABLES.")
    else:
        print("ERROR: video_recordings NOT in _ALLOWED_TABLES.")
        
except Exception as e:
    print(f"FAILED: {e}")
    print("Attempting to create table now...")
    try:
        sql = """
        CREATE TABLE IF NOT EXISTS video_recordings (
            id SERIAL PRIMARY KEY,
            cam_id VARCHAR(50),
            site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
            event_type VARCHAR(50), 
            event_text VARCHAR(100), 
            file_path TEXT UNIQUE NOT NULL,
            thumbnail_path TEXT,
            start_time TIMESTAMP DEFAULT NOW(),
            duration_seconds INTEGER,
            is_favorite BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_video_recordings_cam_time ON video_recordings (cam_id, start_time DESC);
        CREATE INDEX IF NOT EXISTS idx_video_recordings_event ON video_recordings (event_type);
        """
        db._execute_query(sql, commit=True)
        print("SUCCESS: Table created manually.")
    except Exception as e2:
        print(f"CRITICAL ERROR: Could not create table: {e2}")
