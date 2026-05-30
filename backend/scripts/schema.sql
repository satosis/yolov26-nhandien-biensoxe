CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS roles (
    id SERIAL PRIMARY KEY,
    role_name TEXT NOT NULL UNIQUE,
    allowed_menus TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sites (
    id SERIAL PRIMARY KEY,
    site_name TEXT NOT NULL UNIQUE,
    address TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    total_slots INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    role_id INTEGER REFERENCES roles(id) ON DELETE RESTRICT,
    full_name TEXT,
    note TEXT,
    site_ids INTEGER[] DEFAULT ARRAY[]::INTEGER[],
    person_identity TEXT UNIQUE,
    phone TEXT,
    email TEXT,
    date_of_birth DATE,
    id_number TEXT,
    kyc_level INTEGER NOT NULL DEFAULT 0,
    kyc_submitted_at TIMESTAMPTZ,
    kyc_reviewed_at TIMESTAMPTZ,
    kyc_reviewed_by TEXT,
    kyc_reject_reason TEXT,
    remember_token TEXT,
    remember_token_expires TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_role_id ON users(role_id);
CREATE INDEX IF NOT EXISTS idx_users_remember_token ON users(remember_token);

CREATE TABLE IF NOT EXISTS kyc_documents (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    doc_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    reviewed_by TEXT,
    UNIQUE(user_id, doc_type)
);

CREATE TABLE IF NOT EXISTS camera_providers (
    provider_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_base_url TEXT,
    api_doc_url TEXT,
    rtsp_template TEXT,
    onvif_port INTEGER NOT NULL DEFAULT 80,
    default_rtsp_port INTEGER NOT NULL DEFAULT 554,
    default_http_port INTEGER NOT NULL DEFAULT 80,
    notes TEXT,
    logo_url TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS door_providers (
    provider_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    connection_type TEXT,
    api_doc_url TEXT,
    config_template TEXT,
    notes TEXT,
    logo_url TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cameras (
    id SERIAL PRIMARY KEY,
    camera_name TEXT NOT NULL,
    camera_type TEXT NOT NULL DEFAULT 'rtsp',
    imou_device_id TEXT,
    imou_channel_id TEXT NOT NULL DEFAULT '0',
    rtsp_url TEXT,
    onvif_rtsp_detected TEXT,
    camera_ip TEXT,
    camera_port INTEGER NOT NULL DEFAULT 554,
    provider_keys JSONB NOT NULL DEFAULT '{}'::JSONB,
    site_ids INTEGER[] DEFAULT ARRAY[]::INTEGER[],
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_streaming BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cameras_site_ids ON cameras USING GIN(site_ids);
CREATE INDEX IF NOT EXISTS idx_cameras_active ON cameras(is_active);

CREATE TABLE IF NOT EXISTS cameras_asset (
    id SERIAL PRIMARY KEY,
    cam_id TEXT NOT NULL UNIQUE,
    name TEXT,
    ip TEXT,
    mac TEXT,
    serial TEXT,
    model TEXT,
    firmware TEXT,
    rtsp_url TEXT,
    onvif_port INTEGER,
    onvif_profiles JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plate_events (
    id SERIAL PRIMARY KEY,
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    camera_id TEXT,
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    vehicle_type TEXT,
    plate_number TEXT,
    confidence DOUBLE PRECISION,
    image_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plate_events_time ON plate_events(event_time DESC);
CREATE INDEX IF NOT EXISTS idx_plate_events_plate ON plate_events(plate_number);

CREATE TABLE IF NOT EXISTS plate_whitelist (
    id SERIAL PRIMARY KEY,
    plate_number TEXT NOT NULL UNIQUE,
    list_type TEXT NOT NULL DEFAULT 'white',
    owner_name TEXT,
    added_by TEXT,
    site_ids JSONB NOT NULL DEFAULT '[]'::JSONB,
    note TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plate_whitelist_site_ids ON plate_whitelist USING GIN(site_ids);
CREATE INDEX IF NOT EXISTS idx_plate_whitelist_active ON plate_whitelist(is_active);

CREATE TABLE IF NOT EXISTS pending_plates (
    id SERIAL PRIMARY KEY,
    pending_id TEXT NOT NULL UNIQUE,
    event_id INTEGER REFERENCES plate_events(id) ON DELETE SET NULL,
    plate_raw TEXT,
    plate_norm TEXT NOT NULL,
    first_seen_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'pending',
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    image_path TEXT,
    plate_type TEXT NOT NULL DEFAULT 'white',
    confirmed_at_utc TIMESTAMPTZ,
    confirmed_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pending_plates_status_time ON pending_plates(status, first_seen_utc DESC);
CREATE INDEX IF NOT EXISTS idx_pending_plates_plate_norm ON pending_plates(plate_norm);

CREATE TABLE IF NOT EXISTS vehicle_sessions (
    id SERIAL PRIMARY KEY,
    plate_norm TEXT NOT NULL,
    vehicle_type TEXT,
    camera_id_in TEXT,
    camera_id_out TEXT,
    entry_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exit_time TIMESTAMPTZ,
    entry_image TEXT,
    exit_image TEXT,
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'parking',
    stay_duration INTEGER,
    fee DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vehicle_sessions_plate_status ON vehicle_sessions(plate_norm, status);
CREATE INDEX IF NOT EXISTS idx_vehicle_sessions_entry_time ON vehicle_sessions(entry_time DESC);

CREATE TABLE IF NOT EXISTS camera_status_log (
    id SERIAL PRIMARY KEY,
    camera_id TEXT NOT NULL,
    status TEXT,
    event_type TEXT,
    notes TEXT,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_camera_status_log_camera_time ON camera_status_log(camera_id, logged_at DESC);

CREATE TABLE IF NOT EXISTS video_recordings (
    id SERIAL PRIMARY KEY,
    cam_id TEXT,
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    event_type TEXT,
    event_text TEXT,
    file_path TEXT NOT NULL UNIQUE,
    thumbnail_path TEXT,
    start_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_seconds INTEGER,
    file_size_bytes BIGINT,
    is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_video_recordings_cam_time ON video_recordings(cam_id, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_video_recordings_site_time ON video_recordings(site_id, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_video_recordings_event ON video_recordings(event_type);

CREATE TABLE IF NOT EXISTS legal_hold (
    id SERIAL PRIMARY KEY,
    file_path TEXT NOT NULL UNIQUE,
    reason TEXT,
    held_by TEXT,
    held_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS monthly_reports (
    id SERIAL PRIMARY KEY,
    report_month DATE NOT NULL UNIQUE,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS telegram_bots (
    id SERIAL PRIMARY KEY,
    bot_name TEXT NOT NULL UNIQUE,
    token TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT,
    area TEXT,
    height TEXT,
    bays TEXT,
    load_capacity TEXT,
    image TEXT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS news (
    id SERIAL PRIMARY KEY,
    slug TEXT UNIQUE,
    title TEXT NOT NULL,
    category TEXT,
    summary TEXT,
    content TEXT,
    image TEXT,
    publish_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_published BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_publish_date ON news(publish_date DESC);

CREATE TABLE IF NOT EXISTS inquiries (
    id SERIAL PRIMARY KEY,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    company_name TEXT NOT NULL DEFAULT '',
    demand TEXT NOT NULL DEFAULT '',
    source_page TEXT NOT NULL DEFAULT 'home',
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inquiries_read_time ON inquiries(is_read, created_at DESC);

CREATE TABLE IF NOT EXISTS rolling_doors (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    supplier_key TEXT REFERENCES door_providers(provider_key) ON DELETE SET NULL,
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    relay_type TEXT NOT NULL DEFAULT 'http',
    relay_config TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    camera_id INTEGER REFERENCES cameras(id) ON DELETE SET NULL,
    auto_close_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    auto_close_delay_sec INTEGER NOT NULL DEFAULT 300,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rolling_doors_site ON rolling_doors(site_id);

CREATE TABLE IF NOT EXISTS stranger_logs (
    id SERIAL PRIMARY KEY,
    camera_id TEXT,
    similarity DOUBLE PRECISION,
    image_path TEXT,
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    face_embedding BYTEA,
    status TEXT NOT NULL DEFAULT 'new',
    direction TEXT NOT NULL DEFAULT 'frontal',
    converted_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    note TEXT,
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stranger_logs_status_time ON stranger_logs(status, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_stranger_logs_site ON stranger_logs(site_id);

CREATE TABLE IF NOT EXISTS ocr_training_samples (
    id SERIAL PRIMARY KEY,
    plate_image_path TEXT NOT NULL,
    ocr_text TEXT,
    confidence DOUBLE PRECISION,
    camera_id TEXT,
    corrected_text TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ocr_training_samples_status_time ON ocr_training_samples(status, created_at DESC);

CREATE TABLE IF NOT EXISTS cargo_trips (
    id SERIAL PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES plate_whitelist(id) ON DELETE CASCADE,
    plate_number TEXT NOT NULL,
    departure_time TIMESTAMPTZ NOT NULL,
    return_time TIMESTAMPTZ,
    origin TEXT,
    destination TEXT,
    fuel_liters NUMERIC(12,2),
    fuel_price_per_liter NUMERIC(14,2),
    fuel_cost_total NUMERIC(14,2) GENERATED ALWAYS AS (fuel_liters * fuel_price_per_liter) STORED,
    freight_revenue NUMERIC(14,2),
    distance_km NUMERIC(12,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cargo_trips_vehicle_departure ON cargo_trips(vehicle_id, departure_time DESC);

CREATE TABLE IF NOT EXISTS admin_seeds (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
