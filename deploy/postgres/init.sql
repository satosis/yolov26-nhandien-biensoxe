CREATE TABLE IF NOT EXISTS plate_events (
    id              BIGSERIAL PRIMARY KEY,
    camera_id       VARCHAR(50) NOT NULL,
    plate_number    VARCHAR(20),
    vehicle_type    VARCHAR(20) CHECK (vehicle_type IN ('motorbike','car','truck','unknown')),
    confidence      FLOAT,
    direction       VARCHAR(10) CHECK (direction IN ('in','out','unknown')),
    image_path      TEXT,
    crossed_line    BOOLEAN DEFAULT FALSE,
    event_time      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plate_events_event_time ON plate_events (event_time DESC);
CREATE INDEX IF NOT EXISTS idx_plate_events_camera_id ON plate_events (camera_id);
CREATE INDEX IF NOT EXISTS idx_plate_events_plate_number ON plate_events (plate_number);

-- ============================================================
-- Bảng 2: counting_stats — Đếm người/xe qua vạch theo giờ
-- ============================================================
CREATE TABLE IF NOT EXISTS counting_stats (
    id              BIGSERIAL PRIMARY KEY,
    camera_id       VARCHAR(50) NOT NULL,
    stat_date       DATE NOT NULL,
    stat_hour       SMALLINT NOT NULL CHECK (stat_hour BETWEEN 0 AND 23),
    people_in       INTEGER DEFAULT 0,
    people_out      INTEGER DEFAULT 0,
    vehicle_in      INTEGER DEFAULT 0,
    vehicle_out     INTEGER DEFAULT 0,
    motorbike_count INTEGER DEFAULT 0,
    car_count       INTEGER DEFAULT 0,
    truck_count     INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (camera_id, stat_date, stat_hour)
);

CREATE INDEX IF NOT EXISTS idx_counting_stats_date ON counting_stats (stat_date DESC);
CREATE INDEX IF NOT EXISTS idx_counting_stats_camera ON counting_stats (camera_id, stat_date);

-- ============================================================
-- Bảng 3: plate_whitelist — Whitelist/blacklist biển số
-- ============================================================
CREATE TABLE IF NOT EXISTS plate_whitelist (
    id              BIGSERIAL PRIMARY KEY,
    plate_number    VARCHAR(20) NOT NULL UNIQUE,
    list_type       VARCHAR(10) NOT NULL CHECK (list_type IN ('white','black')),
    owner_name      VARCHAR(100),
    note            TEXT,
    added_by        VARCHAR(50),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_whitelist_plate ON plate_whitelist (plate_number) WHERE is_active = TRUE;

-- ============================================================
-- Bảng 4: alert_history — Lịch sử cảnh báo Telegram
-- ============================================================
CREATE TABLE IF NOT EXISTS alert_history (
    id                  BIGSERIAL PRIMARY KEY,
    alert_type          VARCHAR(50) NOT NULL,
    camera_id           VARCHAR(50),
    plate_number        VARCHAR(20),
    message_text        TEXT,
    image_path          TEXT,
    telegram_chat_id    VARCHAR(50),
    telegram_message_id BIGINT,
    sent_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at     TIMESTAMPTZ,
    acknowledged_by     VARCHAR(50)
);

CREATE INDEX IF NOT EXISTS idx_alert_history_sent_at ON alert_history (sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_history_type ON alert_history (alert_type);
CREATE INDEX IF NOT EXISTS idx_alert_history_plate ON alert_history (plate_number);

-- ============================================================
-- Bảng 5: camera_status_log — Trạng thái camera theo thời gian
-- ============================================================
CREATE TABLE IF NOT EXISTS camera_status_log (
    id                  BIGSERIAL PRIMARY KEY,
    camera_id           VARCHAR(50) NOT NULL,
    camera_name         VARCHAR(100),
    status              VARCHAR(20) NOT NULL CHECK (status IN ('online','offline','shift','error')),
    shift_score         FLOAT,
    shift_type          VARCHAR(30),
    baseline_updated_at TIMESTAMPTZ,
    logged_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_camera_status_camera_id ON camera_status_log (camera_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_camera_status_logged_at ON camera_status_log (logged_at DESC);

-- ============================================================
-- Bảng 6: monthly_reports — Báo cáo tháng/ngày đã tổng hợp
-- ============================================================
CREATE TABLE IF NOT EXISTS monthly_reports (
    id              BIGSERIAL PRIMARY KEY,
    report_type     VARCHAR(10) NOT NULL CHECK (report_type IN ('daily','monthly')),
    report_date     DATE NOT NULL,
    camera_id       VARCHAR(50),
    total_vehicles  INTEGER DEFAULT 0,
    total_people    INTEGER DEFAULT 0,
    unknown_plates  INTEGER DEFAULT 0,
    alert_count     INTEGER DEFAULT 0,
    peak_hour       SMALLINT,
    chart_path      TEXT,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (report_type, report_date, camera_id)
);

CREATE INDEX IF NOT EXISTS idx_monthly_reports_date ON monthly_reports (report_date DESC);

-- ============================================================
-- Seed data mẫu
-- ============================================================

-- ============================================================
-- Bảng 7: app_settings — Cấu hình ứng dụng (thay thế .env)
-- ============================================================
CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Bảng 8: roles — Các vai trò hệ thống
-- ============================================================
CREATE TABLE IF NOT EXISTS roles (
    id          SERIAL PRIMARY KEY,
    role_name   VARCHAR(50) UNIQUE NOT NULL,
    description TEXT,
    allowed_menus TEXT DEFAULT 'detection',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO roles (role_name, description, allowed_menus) VALUES
    ('Admin', 'Quyền quản trị toàn hệ thống', '*'),
    ('Manager', 'Quản lý vận hành và dữ liệu', 'detection,dori,asset,sla'),
    ('Security', 'Giám sát an ninh và báo động', 'detection,health'),
    ('Auditor', 'Kiểm toán và báo cáo SLA', 'sla'),
    ('Technician', 'Kỹ thuật viên bảo trì hệ thống', 'health,asset,config'),
    ('Operator', 'Người trực vận hành camera', 'detection'),
    ('Analyst', 'Phân tích dữ liệu nhận diện', 'detection,sla'),
    ('Guest', 'Chỉ xem dữ liệu cơ bản', 'detection'),
    ('Maintenance', 'Vệ sinh và bảo dưỡng phần cứng', 'health'),
    ('Supervisor', 'Giám sát đội ngũ nhân sự', 'detection,sla,users')
ON CONFLICT (role_name) DO NOTHING;

-- ============================================================
-- Bảng 9: users — Quản trị viên và người dùng hệ thống (RBAC)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(50) NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    role_id         INTEGER REFERENCES roles(id),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Seed data mẫu cho 10 Role
INSERT INTO users (username, hashed_password, role_id)
SELECT 
    LOWER(role_name) || '_user', 
    '$2b$12$jCR5nwcHwvk0ww4xdDOpL.XBgBopgAKdC.AarkafM5YZMPt5RrwYW', 
    id 
FROM roles
ON CONFLICT (username) DO NOTHING;

-- Đảm bảo có tài khoản admin chuẩn
UPDATE users SET username = 'admin' WHERE username = 'admin_user';

INSERT INTO app_settings (key, value) VALUES
    ('TELEGRAM_TOKEN', ''),
    ('TELEGRAM_CHAT_IMPORTANT', ''),
    ('TELEGRAM_CHAT_NONIMPORTANT', ''),
    ('RTSP_URL', ''),
    ('CAMERA_MAC', ''),
    ('CAMERA_IP_SUBNET', ''),
    ('OCR_SOURCE', 'rtsp'),
    ('LINE_Y_RATIO', '0.62'),
    ('CAMERA_2_URL', ''),
    ('CAMERA_3_URL', ''),
    ('CAMERA_4_URL', ''),
    ('CAMERA_UI_USER', 'admin'),
    ('CAMERA_UI_PASS', ''),
    ('IMOU_OPEN_APP_ID', ''),
    ('IMOU_OPEN_APP_SECRET', ''),
    ('IMOU_OPEN_DEVICE_ID', '')
ON CONFLICT (key) DO NOTHING;

-- ============================================================
-- Bảng 9: pending_plates — Biển số chờ duyệt
-- ============================================================
CREATE TABLE IF NOT EXISTS pending_plates (
    pending_id       VARCHAR(50) PRIMARY KEY,
    event_id         BIGINT,
    plate_raw        VARCHAR(20),
    plate_norm       VARCHAR(20),
    first_seen_utc   TIMESTAMPTZ DEFAULT NOW(),
    status           VARCHAR(20) DEFAULT 'pending',
    confirmed_at_utc TIMESTAMPTZ,
    confirmed_by     VARCHAR(50)
);

-- ============================================================
-- Bảng 10: legal_hold — Hồ sơ lưu giữ pháp lý
-- ============================================================
CREATE TABLE IF NOT EXISTS legal_hold (
    id          SERIAL PRIMARY KEY,
    file_path   TEXT NOT NULL UNIQUE,
    reason      TEXT,
    held_by     VARCHAR(50),
    held_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ
);
-- ============================================================
-- Bảng 11: telegram_bots — Cấu hình đa Bot Telegram
-- ============================================================
CREATE TABLE IF NOT EXISTS telegram_bots (
    id                  SERIAL PRIMARY KEY,
    bot_name            VARCHAR(100) UNIQUE NOT NULL,
    token               TEXT NOT NULL,
    chat_id_important   VARCHAR(50),
    chat_id_normal      VARCHAR(50),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- Bảng 12: camera_zones — Khu vực camera
-- ============================================================
CREATE TABLE IF NOT EXISTS camera_zones (
    id          SERIAL PRIMARY KEY,
    zone_name   VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO camera_zones (zone_name, description) VALUES
    ('Cổng chính', 'Khu vực cổng chính ra vào'),
    ('Sân sau', 'Khu vực phía sau tòa nhà'),
    ('Kho hàng', 'Khu vực nhà kho'),
    ('Bãi đỗ xe', 'Khu vực bãi đỗ xe'),
    ('Hành lang', 'Khu vực hành lang nội bộ')
ON CONFLICT (zone_name) DO NOTHING;

-- ============================================================
-- Bảng 13: cameras — Quản lý camera
-- ============================================================
CREATE TABLE IF NOT EXISTS cameras (
    id              SERIAL PRIMARY KEY,
    camera_name     VARCHAR(100) NOT NULL,
    camera_type     VARCHAR(50) DEFAULT 'imou',
    imou_device_id  VARCHAR(100),
    imou_channel_id VARCHAR(50) DEFAULT '0',
    rtsp_url        TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    zone_id         INTEGER REFERENCES camera_zones(id),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- APP.PY STATE TABLES (Migrated from SQLite)
-- ============================================================

CREATE TABLE IF NOT EXISTS counters_state (
    id INTEGER PRIMARY KEY,
    people_count INTEGER NOT NULL DEFAULT 0,
    vehicle_count INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO counters_state (id, people_count, vehicle_count, updated_at_utc) 
VALUES (1, 0, 0, NOW()) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS object_tracks (
    track_key VARCHAR(100) PRIMARY KEY,
    label VARCHAR(50),
    last_seen_utc TIMESTAMPTZ,
    last_side VARCHAR(50),
    counted_in INTEGER NOT NULL DEFAULT 0,
    counted_out INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS counter_events (
    id BIGSERIAL PRIMARY KEY,
    ts_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    label VARCHAR(50),
    direction VARCHAR(20),
    delta INTEGER,
    new_count INTEGER,
    track_key VARCHAR(100),
    source VARCHAR(50),
    note TEXT
);

CREATE TABLE IF NOT EXISTS vehicle_exit_sessions (
    session_id VARCHAR(100) PRIMARY KEY,
    started_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    camera VARCHAR(100),
    vehicle_track_key VARCHAR(100),
    active INTEGER NOT NULL DEFAULT 1,
    left_person_decrements INTEGER NOT NULL DEFAULT 0,
    max_left_person_decrements INTEGER NOT NULL DEFAULT 4
);

CREATE TABLE IF NOT EXISTS gate_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    gate_closed INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by VARCHAR(50)
);

INSERT INTO gate_state (id, gate_closed, updated_at_utc, updated_by) 
VALUES (1, 0, NOW(), 'system') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS ptz_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode VARCHAR(50) NOT NULL DEFAULT 'gate',
    ocr_enabled INTEGER NOT NULL DEFAULT 1,
    last_view_utc TIMESTAMPTZ,
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by VARCHAR(50)
);

INSERT INTO ptz_state (id, mode, ocr_enabled, last_view_utc, updated_at_utc, updated_by) 
VALUES (1, 'gate', 1, NULL, NOW(), 'system') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS ptz_events (
    id BIGSERIAL PRIMARY KEY,
    ts_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action VARCHAR(50) NOT NULL,
    reason TEXT,
    prev_mode VARCHAR(50),
    new_mode VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS ptz_test_calls (
    id BIGSERIAL PRIMARY KEY,
    ts_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    preset VARCHAR(100) NOT NULL,
    success INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_key VARCHAR(100) PRIMARY KEY,
    last_sent_utc TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS person_sessions (
    id BIGSERIAL PRIMARY KEY,
    person_key VARCHAR(100),
    camera VARCHAR(100),
    entered_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exited_at_utc TIMESTAMPTZ,
    source VARCHAR(50),
    confidence REAL
);

CREATE TABLE IF NOT EXISTS vehicle_sessions (
    id BIGSERIAL PRIMARY KEY,
    vehicle_key VARCHAR(100),
    plate_norm VARCHAR(20),
    vehicle_type VARCHAR(50),
    camera VARCHAR(100),
    entered_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exited_at_utc TIMESTAMPTZ,
    time_outside_seconds INTEGER,
    source VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS driver_attribution (
    id BIGSERIAL PRIMARY KEY,
    ts_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    direction VARCHAR(20) NOT NULL,
    person_identity VARCHAR(100),
    vehicle_identity VARCHAR(100),
    vehicle_session_id BIGINT,
    person_session_id BIGINT,
    evidence_json TEXT
);

CREATE TABLE IF NOT EXISTS gate_alert_events (
    id BIGSERIAL PRIMARY KEY,
    ts_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    gate_closed INTEGER NOT NULL,
    people_count INTEGER NOT NULL,
    note TEXT,
    snapshot_path TEXT
);

CREATE TABLE IF NOT EXISTS daily_aggregates (
    day_utc DATE NOT NULL,
    person_identity VARCHAR(100) NOT NULL,
    vehicle_identity VARCHAR(100) NOT NULL,
    direction VARCHAR(20) NOT NULL,
    trips_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(day_utc, person_identity, vehicle_identity, direction)
);

CREATE TABLE IF NOT EXISTS people_whitelist (
    person_identity VARCHAR(100) PRIMARY KEY,
    note TEXT,
    added_at_utc TIMESTAMPTZ DEFAULT NOW(),
    added_by VARCHAR(50)
);

CREATE INDEX IF NOT EXISTS idx_counter_events_ts_utc ON counter_events (ts_utc);
CREATE INDEX IF NOT EXISTS idx_vehicle_exit_sessions_active ON vehicle_exit_sessions (active);
CREATE INDEX IF NOT EXISTS idx_person_sessions_entered ON person_sessions (entered_at_utc);
CREATE INDEX IF NOT EXISTS idx_vehicle_sessions_entered ON vehicle_sessions (entered_at_utc);
CREATE INDEX IF NOT EXISTS idx_driver_attribution_ts_utc ON driver_attribution (ts_utc);
CREATE INDEX IF NOT EXISTS idx_driver_attribution_person ON driver_attribution (person_identity);
CREATE INDEX IF NOT EXISTS idx_driver_attribution_vehicle ON driver_attribution (vehicle_identity);
CREATE INDEX IF NOT EXISTS idx_gate_alert_events_ts_utc ON gate_alert_events (ts_utc);
