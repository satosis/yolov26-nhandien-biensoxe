-- Initial Schema for Camera AI Parking System
-- Optimized for PostgreSQL 16

CREATE TABLE IF NOT EXISTS roles (
    id SERIAL PRIMARY KEY,
    role_name VARCHAR(50) UNIQUE NOT NULL,
    allowed_menus TEXT, -- Comma separated or *
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO roles (role_name, allowed_menus) VALUES 
('Admin', '*'),
('Manager', 'detection,parking_dashboard,parking_logs,cameras,zones,vehicles'),
('Guest', 'detection,parking_dashboard')
ON CONFLICT (role_name) DO NOTHING;

CREATE TABLE IF NOT EXISTS camera_zones (
    id SERIAL PRIMARY KEY,
    zone_name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    total_slots INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cameras (
    id SERIAL PRIMARY KEY,
    camera_name VARCHAR(100) NOT NULL,
    rtsp_url TEXT NOT NULL,
    zone_id INTEGER REFERENCES camera_zones(id),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    hashed_password TEXT,
    full_name VARCHAR(100),
    role_id INTEGER REFERENCES roles(id),
    zone_ids INTEGER[], -- Array of allowed zones
    note TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Default admin: admin / admin123 (hashed if needed, but here as plain or personnel_only if personnel)
INSERT INTO users (username, hashed_password, role_id, full_name) 
VALUES ('admin', 'pbkdf2:sha256:600000$mock_hash', 1, 'System Administrator')
ON CONFLICT (username) DO NOTHING;

CREATE TABLE IF NOT EXISTS plate_events (
    id SERIAL PRIMARY KEY,
    event_time TIMESTAMP DEFAULT NOW(),
    camera_id VARCHAR(50),
    vehicle_type VARCHAR(50),
    plate_norm VARCHAR(20),
    plate_number VARCHAR(20), -- Raw
    confidence FLOAT,
    image_path TEXT,
    ai_audit_result TEXT,
    ai_audit_mismatch BOOLEAN DEFAULT FALSE,
    ai_audited_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vehicle_sessions (
    id SERIAL PRIMARY KEY,
    plate_norm VARCHAR(20) NOT NULL,
    entry_time TIMESTAMP DEFAULT NOW(),
    exit_time TIMESTAMP,
    camera_id_in INTEGER REFERENCES cameras(id),
    camera_id_out INTEGER REFERENCES cameras(id),
    zone_id INTEGER REFERENCES camera_zones(id),
    entry_image TEXT,
    exit_image TEXT,
    status VARCHAR(20) DEFAULT 'parking', -- parking, completed
    stay_duration INTEGER, -- seconds
    fee NUMERIC(15,2) DEFAULT 0
);

CREATE TABLE IF NOT EXISTS plate_whitelist (
    id SERIAL PRIMARY KEY,
    plate_number VARCHAR(20) NOT NULL,
    zone_id INTEGER REFERENCES camera_zones(id),
    list_type VARCHAR(20) DEFAULT 'white',
    owner_name VARCHAR(100),
    added_by VARCHAR(50),
    note TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP,
    UNIQUE (plate_number, zone_id)
);

CREATE TABLE IF NOT EXISTS pending_plates (
    pending_id VARCHAR(50) PRIMARY KEY,
    event_id VARCHAR(50),
    plate_raw VARCHAR(20),
    plate_norm VARCHAR(20),
    first_seen_utc TIMESTAMP,
    status VARCHAR(20) DEFAULT 'pending',
    confirmed_at_utc TIMESTAMP,
    confirmed_by VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS camera_status_log (
    id SERIAL PRIMARY KEY,
    camera_id VARCHAR(50),
    status VARCHAR(50),
    logged_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS legal_hold (
    file_path TEXT PRIMARY KEY,
    reason TEXT,
    held_by VARCHAR(50),
    held_at TIMESTAMP DEFAULT NOW(),
    released_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS monthly_reports (
    id SERIAL PRIMARY KEY,
    report_type VARCHAR(20),
    report_date DATE,
    camera_id VARCHAR(50),
    total_vehicles INTEGER,
    UNIQUE(report_type, report_date, camera_id)
);

CREATE TABLE IF NOT EXISTS telegram_bots (
    bot_name VARCHAR(50) PRIMARY KEY,
    token TEXT NOT NULL,
    chat_id_important VARCHAR(50),
    chat_id_normal VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cameras_asset (
    cam_id          VARCHAR(50) PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    ip              VARCHAR(50),
    mac             VARCHAR(20),
    serial          VARCHAR(100),
    model           VARCHAR(100),
    firmware        VARCHAR(100),
    rtsp_url        TEXT,
    install_date    TEXT,
    location_lat    REAL,
    location_lon    REAL,
    mount_height_m  REAL,
    fov_deg         REAL,
    dori_class      VARCHAR(50),
    notes           TEXT,
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_ip ON cameras_asset (ip);
