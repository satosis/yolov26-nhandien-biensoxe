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
('Manager', 'detection,parking_dashboard,parking_logs,cameras,sites,vehicles'),
('Guest', 'detection,parking_dashboard')
ON CONFLICT (role_name) DO NOTHING;

-- Sites (physical parking locations)
CREATE TABLE IF NOT EXISTS sites (
    id SERIAL PRIMARY KEY,
    site_name VARCHAR(100) UNIQUE NOT NULL,
    address TEXT,
    description TEXT,
    total_slots INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Seeder: default sites
INSERT INTO sites (site_name, address, description, total_slots) VALUES
('Lán', 'Đường 303', 'Khu vực lán', 0),
('Nhà ở Tề Lỗ', 'Tề Lỗ', 'Nhà ở', 0)
ON CONFLICT (site_name) DO NOTHING;

CREATE TABLE IF NOT EXISTS cameras (
    id SERIAL PRIMARY KEY,
    camera_name VARCHAR(100) NOT NULL,
    camera_type VARCHAR(50) DEFAULT 'rtsp',
    imou_device_id VARCHAR(100),
    imou_channel_id VARCHAR(50) DEFAULT '0',
    rtsp_url TEXT,
    site_ids INTEGER[],
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP
);

-- Seeder: default camera (Imou at Lán, site_id=1)
INSERT INTO cameras (camera_name, camera_type, imou_device_id, imou_channel_id, rtsp_url, site_ids) VALUES
('Cam cổng chính', 'imou', '5552ABJPSFFB063', '0', 'rtsp://admin:password@192.168.1.100:554/cam/realmonitor?channel=1&subtype=0', ARRAY[1])
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    hashed_password TEXT,
    full_name VARCHAR(100),
    role_id INTEGER REFERENCES roles(id),
    site_ids INTEGER[], -- Array of allowed sites
    note TEXT,
    remember_token VARCHAR(64) UNIQUE,
    remember_token_expires TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Default users: admin, manager, guest (bcrypt hash of 'bB123456789@')
INSERT INTO users (username, hashed_password, role_id, full_name) VALUES 
('admin', '$2b$12$iUEGOb7Vw9ox6PIBJ/N4NOLXA8CBRpax7uYhRAATMJgulgzrB4WZ2', 1, 'System Administrator'),
('manager', '$2b$12$iUEGOb7Vw9ox6PIBJ/N4NOLXA8CBRpax7uYhRAATMJgulgzrB4WZ2', 2, 'System Manager'),
('guest', '$2b$12$iUEGOb7Vw9ox6PIBJ/N4NOLXA8CBRpax7uYhRAATMJgulgzrB4WZ2', 3, 'System Guest')
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
    ai_audited_at TIMESTAMP,
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS vehicle_sessions (
    id SERIAL PRIMARY KEY,
    plate_norm VARCHAR(20) NOT NULL,
    entry_time TIMESTAMP DEFAULT NOW(),
    exit_time TIMESTAMP,
    camera_id_in INTEGER REFERENCES cameras(id) ON DELETE SET NULL,
    camera_id_out INTEGER REFERENCES cameras(id) ON DELETE SET NULL,
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    entry_image TEXT,
    exit_image TEXT,
    status VARCHAR(20) DEFAULT 'parking', -- parking, completed
    stay_duration INTEGER, -- seconds
    fee NUMERIC(15,2) DEFAULT 0
);

CREATE TABLE IF NOT EXISTS plate_whitelist (
    id SERIAL PRIMARY KEY,
    plate_number VARCHAR(20) UNIQUE NOT NULL,
    site_ids JSONB,
    list_type VARCHAR(20) DEFAULT 'white',
    owner_name VARCHAR(100),
    added_by VARCHAR(50),
    note TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_whitelist_site_ids ON plate_whitelist USING GIN (site_ids);

-- Seeder: default whitelist plates
INSERT INTO plate_whitelist (plate_number, site_ids, owner_name, added_by, note) VALUES
('88C07304', '[1, 2]', 'Nguyễn Kim Quyết', 'admin', 'Xe ở Lán + Nhà')
ON CONFLICT (plate_number) DO NOTHING;

CREATE TABLE IF NOT EXISTS pending_plates (
    pending_id VARCHAR(50) PRIMARY KEY,
    event_id VARCHAR(50),
    plate_raw VARCHAR(20),
    plate_norm VARCHAR(20),
    first_seen_utc TIMESTAMP,
    status VARCHAR(20) DEFAULT 'pending',
    confirmed_at_utc TIMESTAMP,
    confirmed_by VARCHAR(50),
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Seeder: default API key placeholders for each camera provider
-- Imou (cloud API)
INSERT INTO app_settings (key, value) VALUES
('IMOU_APP_ID', 'lc9f9e2d2dcc094a61'), ('IMOU_APP_SECRET', 'lc9f9e2d2dcc094a61'), ('IMOU_MK', 'bB123456789@'), ('IMOU_DEVICE_ID', '5552ABJPSFFB063'), ('IMOU_EMAIL', 'hungnmk5@gmail.com')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

-- Hikvision (local + optional cloud)
INSERT INTO app_settings (key, value) VALUES
('HIKVISION_USERNAME', ''), ('HIKVISION_PASSWORD', ''), ('HIKVISION_HOST', ''),
('HIKVISION_APP_KEY', ''), ('HIKVISION_APP_SECRET', ''), ('HIKVISION_EMAIL', '')
ON CONFLICT (key) DO NOTHING;

-- Dahua (local + optional DSS cloud)
INSERT INTO app_settings (key, value) VALUES
('DAHUA_USERNAME', ''), ('DAHUA_PASSWORD', ''), ('DAHUA_HOST', ''),
('DAHUA_CLIENT_ID', ''), ('DAHUA_CLIENT_SECRET', ''), ('DAHUA_EMAIL', '')
ON CONFLICT (key) DO NOTHING;

-- Reolink (local only)
INSERT INTO app_settings (key, value) VALUES
('REOLINK_USERNAME', ''), ('REOLINK_PASSWORD', ''), ('REOLINK_HOST', '')
ON CONFLICT (key) DO NOTHING;

-- Tapo / VIGI (local + ONVIF)
INSERT INTO app_settings (key, value) VALUES
('TAPO_USERNAME', ''), ('TAPO_PASSWORD', ''), ('TAPO_HOST', ''), ('TAPO_ONVIF_PORT', '2020')
ON CONFLICT (key) DO NOTHING;

-- Axis (local VAPIX)
INSERT INTO app_settings (key, value) VALUES
('AXIS_USERNAME', ''), ('AXIS_PASSWORD', ''), ('AXIS_HOST', ''), ('AXIS_API_KEY', '')
ON CONFLICT (key) DO NOTHING;

-- Uniview (local + optional cloud)
INSERT INTO app_settings (key, value) VALUES
('UNIVIEW_USERNAME', ''), ('UNIVIEW_PASSWORD', ''), ('UNIVIEW_HOST', ''),
('UNIVIEW_APP_ID', ''), ('UNIVIEW_APP_SECRET', ''), ('UNIVIEW_EMAIL', '')
ON CONFLICT (key) DO NOTHING;

-- Hanwha (local SUNAPI + optional STEP)
INSERT INTO app_settings (key, value) VALUES
('HANWHA_USERNAME', ''), ('HANWHA_PASSWORD', ''), ('HANWHA_HOST', ''),
('HANWHA_API_KEY', ''), ('HANWHA_EMAIL', '')
ON CONFLICT (key) DO NOTHING;

-- Bosch (local RCP+ / ONVIF)
INSERT INTO app_settings (key, value) VALUES
('BOSCH_USERNAME', ''), ('BOSCH_PASSWORD', ''), ('BOSCH_HOST', ''), ('BOSCH_SDK_TOKEN', '')
ON CONFLICT (key) DO NOTHING;

-- RTSP Generic
INSERT INTO app_settings (key, value) VALUES
('RTSP_USERNAME', ''), ('RTSP_PASSWORD', ''), ('RTSP_RTSP_URL', '')
ON CONFLICT (key) DO NOTHING;

-- ONVIF Generic
INSERT INTO app_settings (key, value) VALUES
('ONVIF_USERNAME', ''), ('ONVIF_PASSWORD', ''), ('ONVIF_HOST', ''), ('ONVIF_ONVIF_PORT', '80')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS camera_status_log (
    id SERIAL PRIMARY KEY,
    camera_id VARCHAR(50),
    status VARCHAR(50),
    event_type VARCHAR(50),
    ended_at TIMESTAMP,
    duration_seconds INTEGER,
    notes TEXT,
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
    chat_id VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO telegram_bots (bot_name, token, chat_id) VALUES
('Cảnh báo:Quan trọng', '7525014385:AAFOoctmRZUNS8_xt6nRzVBs2gIuLSSlVDc', '-5273529392'),
('Cảnh báo: Không quan trọng', '7525014385:AAFOoctmRZUNS8_xt6nRzVBs2gIuLSSlVDc', '-5171619580')
ON CONFLICT (bot_name) DO NOTHING;

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

-- Performance Indexes from Optimization Plan
CREATE INDEX IF NOT EXISTS idx_vehicle_sessions_plate_status ON vehicle_sessions (plate_norm, status);
CREATE INDEX IF NOT EXISTS idx_vehicle_sessions_entered ON vehicle_sessions (entry_time);
CREATE INDEX IF NOT EXISTS idx_plate_events_time_conf ON plate_events (event_time, confidence);
CREATE INDEX IF NOT EXISTS idx_users_site_ids ON users USING GIN (site_ids);
CREATE INDEX IF NOT EXISTS idx_cameras_site_ids ON cameras USING GIN (site_ids);

-- WebRTC TURN server config
INSERT INTO app_settings (key, value) VALUES
('WEBRTC_TURN_URL', ''), ('WEBRTC_TURN_USERNAME', ''), ('WEBRTC_TURN_PASSWORD', '')
ON CONFLICT (key) DO NOTHING;

-- Camera Providers (manufacturer/brand management)
CREATE TABLE IF NOT EXISTS camera_providers (
    id SERIAL PRIMARY KEY,
    provider_key VARCHAR(50) UNIQUE NOT NULL,  -- e.g. 'imou', 'hikvision'
    name VARCHAR(100) NOT NULL,
    logo_url TEXT,
    api_base_url TEXT,
    api_doc_url TEXT,
    rtsp_template TEXT,  -- e.g. rtsp://{user}:{pass}@{ip}:554/cam/realmonitor?channel=1
    onvif_port INTEGER DEFAULT 80,
    default_rtsp_port INTEGER DEFAULT 554,
    default_http_port INTEGER DEFAULT 80,
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Seeder: popular camera providers
INSERT INTO camera_providers (provider_key, name, api_base_url, api_doc_url, rtsp_template, onvif_port, default_rtsp_port, default_http_port, notes) VALUES
('imou',       'Imou (Dahua Consumer)',    'https://open.imoulife.com',                 'https://open.imoulife.com/book/index.html',                          'rtsp://{user}:{pass}@{ip}:554/cam/realmonitor?channel=1&subtype=0', 80,   554, 80,   'Imou Open Platform: đăng ký App ID + App Secret tại https://open.imoulife.com'),
('hikvision',  'Hikvision',               'https://open.hikvision.com',                'https://tpp.hikvision.com',                                          'rtsp://{user}:{pass}@{ip}:554/Streaming/Channels/101',              80,   554, 80,   'ISAPI local + HikConnect Cloud. Partner portal: https://tpp.hikvision.com'),
('dahua',      'Dahua (Pro / DSS)',        'https://open.dahuasecurity.com',            'https://www.dahuasecurity.com/developer',                            'rtsp://{user}:{pass}@{ip}:554/cam/realmonitor?channel=1&subtype=0', 80,   554, 80,   'DSS Open Platform. Tài liệu SDK: https://www.dahuasecurity.com/developer'),
('reolink',    'Reolink',                 NULL,                                        'https://community.reolink.com/topic/4196',                           'rtsp://{user}:{pass}@{ip}:554/h264Preview_01_main',                 8000, 554, 80,   'Local HTTP API (không cần đăng ký). Tài liệu: https://community.reolink.com/topic/4196'),
('tapo',       'TP-Link Tapo / VIGI',     NULL,                                        'https://www.tp-link.com/en/support/download/tapo-c200/',             'rtsp://{user}:{pass}@{ip}:554/stream1',                             2020, 554, 80,   'Local API + ONVIF (VIGI). Không có cloud API công khai. ONVIF port 2020'),
('axis',       'Axis Communications',     'https://developer.axis.com',               'https://developer.axis.com/vapix',                                   'rtsp://{user}:{pass}@{ip}:554/axis-media/media.amp',                80,   554, 80,   'VAPIX API tại https://developer.axis.com. Hỗ trợ ONVIF Profile S/T'),
('uniview',    'Uniview (UNV)',            'https://global-open.uniview.com',           'https://global-open.uniview.com',                                    'rtsp://{user}:{pass}@{ip}:554/unicast/c1/s0/live',                  80,   554, 80,   'Unisee Open Platform tại https://global-open.uniview.com. Hỗ trợ ONVIF'),
('hanwha',     'Hanwha Vision (Samsung)', 'https://step.hanwha-security.com',          'https://step.hanwha-security.com',                                   'rtsp://{user}:{pass}@{ip}:554/profile1/media.smp',                  80,   554, 80,   'SUNAPI + STEP Portal tại https://step.hanwha-security.com. Hỗ trợ ONVIF Profile S'),
('bosch',      'Bosch Security',          'https://downloadstore.boschsecurity.com',   'https://community.boschsecurity.com',                                'rtsp://{user}:{pass}@{ip}:554/rtsp_tunnel',                         80,   554, 80,   'RCP+ SDK tại https://downloadstore.boschsecurity.com. Hỗ trợ ONVIF'),
('rtsp',       'RTSP Generic',            NULL,                                        NULL,                                                                 'rtsp://{user}:{pass}@{ip}:554/stream',                              80,   554, 80,   'Camera RTSP bất kỳ, không cần API'),
('onvif',      'ONVIF Generic',           'https://www.onvif.org',                     'https://www.onvif.org/profiles/specifications/',                      'rtsp://{user}:{pass}@{ip}:554/stream',                              80,   554, 80,   'Chuẩn ONVIF Profile S/T/G. Device Discovery qua WS-Discovery')
ON CONFLICT (provider_key) DO UPDATE SET
    name = EXCLUDED.name,
    api_base_url = EXCLUDED.api_base_url,
    api_doc_url = EXCLUDED.api_doc_url,
    rtsp_template = EXCLUDED.rtsp_template,
    onvif_port = EXCLUDED.onvif_port,
    default_rtsp_port = EXCLUDED.default_rtsp_port,
    default_http_port = EXCLUDED.default_http_port,
    notes = EXCLUDED.notes,
    updated_at = NOW();

-- Stranger face detection logs
CREATE TABLE IF NOT EXISTS stranger_logs (
    id SERIAL PRIMARY KEY,
    event_time TIMESTAMP DEFAULT NOW(),
    camera_id VARCHAR(50),
    similarity FLOAT,
    image_path TEXT,
    face_embedding BYTEA,
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    status VARCHAR(20) DEFAULT 'new',  -- new, reviewed, converted (da tao user)
    converted_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_stranger_logs_time ON stranger_logs(event_time DESC);
CREATE INDEX IF NOT EXISTS idx_stranger_logs_status ON stranger_logs(status);

-- Door/Gate Providers (manufacturer/brand management)
CREATE TABLE IF NOT EXISTS door_providers (
    id SERIAL PRIMARY KEY,
    provider_key VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    connection_type VARCHAR(50),
    api_doc_url TEXT,
    config_template TEXT,
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO door_providers (provider_key, name, connection_type, api_doc_url, notes) VALUES
('tuya',      'Tuya Smart',       'tuya_local',  'https://developer.tuya.com/en/docs/iot',                          'Tuya Local SDK (tinytuya). Cần Device ID + Local Key từ Tuya IoT Platform'),
('tasmota',   'Tasmota (ESP)',     'http',        'https://tasmota.github.io/docs/',                                'Flash firmware Tasmota lên ESP8266/ESP32. Điều khiển qua HTTP cmnd API'),
('shelly',    'Shelly',            'http',        'https://shelly-api-docs.shelly.cloud/',                          'Shelly Gen1/Gen2 HTTP API. Không cần flash firmware, hoạt động out-of-box'),
('sonoff',    'Sonoff eWeLink',    'http',        'https://coolkit-technologies.github.io/eWeLink-API/',            'Sonoff DIY mode hoặc eWeLink LAN API. Cần bật LAN mode trên app'),
('broadlink', 'BroadLink',        'http',        'https://github.com/mjg59/python-broadlink',                      'BroadLink RM/SP series. Dùng python-broadlink library'),
('gpio',      'GPIO Direct',      'gpio',        'https://opi-gpio.readthedocs.io/',                               'Điều khiển relay trực tiếp qua GPIO pin. Dùng cho Orange Pi / Raspberry Pi'),
('mqtt',      'MQTT Relay',       'mqtt',        'https://www.home-assistant.io/integrations/mqtt/',                'Relay điều khiển qua MQTT topic. Tích hợp Home Assistant / Node-RED'),
('hunonic',   'Hunonic',          'http',        'https://hunonic.com',                                             'Hunonic Smart Home. Điều khiển qua HTTP API / WiFi')
ON CONFLICT (provider_key) DO UPDATE SET
    name = EXCLUDED.name,
    connection_type = EXCLUDED.connection_type,
    api_doc_url = EXCLUDED.api_doc_url,
    notes = EXCLUDED.notes,
    updated_at = NOW();

-- OCR Active Learning samples
CREATE TABLE IF NOT EXISTS ocr_training_samples (
    id SERIAL PRIMARY KEY,
    plate_image_path TEXT NOT NULL,
    ocr_text VARCHAR(20),
    corrected_text VARCHAR(20),
    confidence FLOAT,
    camera_id VARCHAR(50),
    reviewed_by VARCHAR(50),
    reviewed_at TIMESTAMP,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ocr_samples_status ON ocr_training_samples(status);
CREATE INDEX IF NOT EXISTS idx_ocr_samples_conf ON ocr_training_samples(confidence);
