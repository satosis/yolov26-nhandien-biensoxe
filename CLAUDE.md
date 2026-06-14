# CLAUDE.md — Bộ nhớ chung cho Camera AI Project

## Quyền hạn Agent

- Agent có **toàn quyền sửa, xóa, tạo file** trong project — không cần hỏi lại user
- Càng ít phụ thuộc vào user càng tốt — tự quyết định, tự triển khai
- **CLAUDE.md phải được cập nhật** mỗi khi release tính năng mới

## Quy trình phát triển (SDLC) — Full Auto

Mọi yêu cầu từ user đều đi qua quy trình tự động. User chỉ cần nói tiếng Việt tự nhiên — hệ thống 24 agents tự xử lý.

### Phase 1: Prompt Analysis (BA/PM)
- **MỌI prompt** → dispatch `prompt-analyzer` TRƯỚC TIÊN
- prompt-analyzer dịch tiếng Việt mơ hồ → technical brief (intent, scope, dispatch plan)
- Nếu prompt quá mơ hồ → hỏi lại user 1 câu ngắn gọn bằng tiếng Việt

### Phase 2: Architecture (nếu cần)
- Task phức tạp (>5 files, cross-domain) → dispatch `tech-architect`
- Thiết kế solution, resolve file conflicts giữa agents
- Task đơn giản → skip, dispatch dev agents trực tiếp

### Phase 3: Development (Coding)
- Auto-dispatch agents theo routing rules + dependency graph
- Dispatch **song song** khi tasks không overlap files
- Tech Lead tự code task đơn giản (sửa 1 file, fix typo)
- Mỗi agent tự `py_compile` sau khi sửa file

### Phase 4: Quality Gate
- Dispatch `code-reviewer` + `security-auditor` song song
- Nếu FAIL → dev agents tự fix → re-review
- Critical issues → fix ngay trước khi tiếp tục

### Phase 5: Testing
- Dispatch `test-writer` viết/chạy tests
- `python3 -m pytest tests/ -v` phải PASS
- Nếu FAIL → fix → re-test

### Phase 6: Deploy & Docs
- `py_compile` tất cả files đã sửa
- Dispatch `infrastructure` nếu cần deploy changes
- Dispatch `doc-writer` cập nhật CLAUDE.md

### Phase 7: Meta-review
- `self-improver` phân tích lỗi lặp, đề xuất cải thiện
- `monitoring-agent` verify system health sau deploy

## Hệ thống 24 Agents

### Phân cấp tổ chức:
```
Tech Lead (main agent) — orchestrate toàn bộ
│
├── Nhóm Điều phối
│   ├── prompt-analyzer (BA/PM) — LUÔN chạy đầu tiên
│   ├── product-owner (PO) — roadmap, đề xuất tính năng
│   └── tech-architect (SA) — kiến trúc, resolve conflicts
│
├── Nhóm Nghiên cứu
│   ├── market-researcher — cào tính năng từ nền tảng camera/parking thực
│   └── feature-scout — so sánh đối thủ, gap analysis, đề xuất tính năng
│
├── Nhóm Development
│   ├── ai-detection (AI Engineer) — YOLO, OCR, face, tripwire
│   ├── backend-services (Backend Dev) — DB methods, services, Telegram
│   ├── frontend-ui (Frontend Dev) — FastAPI UI, Jinja2 templates, HTMX, Tailwind CSS
│   ├── database-postgres (DBA) — schema, migrations, queries
│   ├── infrastructure (DevOps) — Docker, deploy, scripts
│   ├── iot-integration (IoT Engineer) — cửa cuốn, barrier, relay, MQTT
│   └── camera-specialist (Camera Engineer) — RTSP, ONVIF, PTZ, WebRTC
│
├── Nhóm Quality & Security
│   ├── code-reviewer (QA Lead) — review code, CHỈ ĐỌC
│   ├── test-writer (QA Engineer) — viết + chạy tests
│   ├── security-auditor (Security) — OWASP, credentials, injection
│   ├── performance-optimizer (Perf) — ARM profiling, memory, latency
│   └── error-handler (Reliability) — retry, fallback, circuit breaker
│
├── Nhóm Vận hành
│   ├── monitoring-agent (SRE) — health checks, uptime, disk
│   ├── data-analyst (Analytics) — thống kê, trends, insights
│   ├── alert-manager (Alerts) — Telegram routing, escalation, dedup
│   └── backup-recovery (Backup) — DB backup, retention, disaster recovery
│
└── Nhóm UX & Meta
    ├── ux-advisor (UX) — mobile-first, accessibility, Vietnamese UI
    ├── doc-writer (Docs) — CLAUDE.md, user guide, changelog
    └── self-improver (Meta) — phân tích lỗi lặp, cải thiện agents
```

### Routing Rules (Auto-dispatch):

MỌI PROMPT → `prompt-analyzer` TRƯỚC → technical brief → dispatch theo brief

| User nói (tiếng Việt tự nhiên) | Agent(s) được dispatch |
|---|---|
| YOLO, model, OCR, biển số, nhận diện, face, stranger, tripwire, vạch ảo, threshold | **ai-detection** |
| FastAPI, API, Telegram, service, whitelist, settings, app_settings | **backend-services** |
| FastAPI, UI, dashboard, view, form, login, sidebar, menu, CSS, trang, màn hình, Jinja2, HTMX, Tailwind | **frontend-ui** |
| schema, bảng, table, migration, SQL, index, query, PostgreSQL, init.sql | **database-postgres** |
| Docker, Dockerfile, compose, deploy, container, port, install, requirements | **infrastructure** |
| cửa cuốn, barrier, relay, cảm biến, Tuya, Tasmota, Shelly, GPIO, MQTT | **iot-integration** |
| camera, RTSP, ONVIF, PTZ, Imou, WebRTC, stream, kết nối camera | **camera-specialist** |
| test, pytest, coverage, fixture, mock | **test-writer** |
| review, kiểm tra code, audit | **code-reviewer** |
| bảo mật, hack, credentials, injection | **security-auditor** |
| chậm, lag, memory, CPU, tối ưu | **performance-optimizer** |
| lỗi, crash, không chạy, bug | **error-handler** |
| health, uptime, disk, monitoring | **monitoring-agent** |
| thống kê, báo cáo, trends, analytics | **data-analyst** |
| cảnh báo, alert, Telegram routing | **alert-manager** |
| backup, sao lưu, restore, mất data | **backup-recovery** |
| giao diện khó dùng, UX, mobile, accessibility | **ux-advisor** |
| hướng dẫn, cách dùng, documentation | **doc-writer** |
| nên làm gì tiếp, roadmap, tính năng mới | **product-owner** |
| cải thiện agent, refactor, duplication | **self-improver** |
| đối thủ có gì, tính năng camera khác, so sánh, thị trường | **market-researcher** → **feature-scout** |
| tính năng nào nên thêm, gap, thiếu gì, đối thủ hơn gì | **feature-scout** |

### Dispatch Order (Dependency Graph):
```
prompt-analyzer → tech-architect (nếu complex)
  → database-postgres (nếu schema change)
  → [parallel] backend-services + iot-integration + camera-specialist + ai-detection
  → frontend-ui
  → [parallel] code-reviewer + security-auditor
  → test-writer → performance-optimizer (nếu cần)
  → infrastructure (deploy) → doc-writer + monitoring-agent

Research flow (khi cần nghiên cứu thị trường):
  prompt-analyzer → market-researcher (cào data từ web)
  → feature-scout (so sánh + gap analysis)
  → product-owner (quyết định priority)
  → tech-architect (thiết kế solution)
  → dev agents...
```

### Multi-agent dispatch:
- Task liên quan nhiều domain → dispatch **song song** (không overlap files)
- VD: "Thêm loại cửa cuốn mới + UI" → iot-integration + database-postgres + frontend-ui
- VD: "Đối thủ có gì hay" → market-researcher → feature-scout → product-owner
- Tech Lead tổng hợp kết quả, resolve conflicts

### Khi KHÔNG cần dispatch:
- Task đơn giản (sửa 1 file, fix typo) → Tech Lead tự làm
- Câu hỏi/giải thích → Tech Lead trả lời trực tiếp
- Research/explore → dùng Explore agent

## Tổng quan hệ thống

Hệ thống giám sát bãi giữ xe thông minh chạy trên **Orange Pi 4 Pro (ARM)**, tích hợp:
- Nhận diện biển số xe Việt Nam (YOLOv26 + RapidOCR)
- Đếm người/xe qua vạch ảo (TripwireTracker)
- Phát hiện camera lệch góc (ORB feature matching + RANSAC)
- Điều khiển cửa cuốn tự động (Tuya)
- Cảnh báo Telegram (2 kênh: quan trọng / thường)
- PTZ camera control qua ONVIF + Imou Open API
- Quản lý toàn bộ hệ thống qua FastAPI + Jinja2 + HTMX + Tailwind CSS
- Nhận diện khuôn mặt (InsightFace buffalo_sc)
- Phát hiện người lạ (stranger detection, threshold 0.25)
- Remember token login (, localStorage + DB)
- Camera provider management (DB-driven, không hardcode)
- RBAC phân quyền menu (DB-driven)
- Multi-site management (nhiều bãi xe, mỗi site có zone/camera riêng)
- Mobile WebRTC camera view (TURN server, facingMode environment)

## Stack công nghệ

| Thành phần | Công nghệ |
|---|---|
| AI inference | YOLOv26 ONNX Runtime, RapidOCR, InsightFace |
| Backend | FastAPI + uvicorn |
| Camera | OpenCV RTSP, ONVIF, Imou Open API |
| UI/Management | FastAPI + Jinja2 + HTMX + Tailwind CSS |
| Messaging | Telegram Bot API |
| Database | PostgreSQL |
| Container | Docker Compose |

## Kiến trúc tổng thể

```
RTSP Camera (Imou 2K)
    │
    ├─► main.py (AI core: YOLO track + OCR + TripwireTracker)
    │       ├─► core/database.py (PostgreSQL)
    │       ├─► core/telegram_service.py (cảnh báo)
    │       ├─► core/door_auto_close.py (tự đóng cửa khi không có người)
    │       └─► core/door_controller.py (điều khiển relay: Tuya/Tasmota/GPIO/HTTP/Hunonic)
    │
    └─► frontend/ (FastAPI app — quản lý toàn bộ hệ thống)
            ├─► frontend/menu_registry.py (single source of truth cho menus)
            ├─► frontend/routers/ (21 route handlers)
            └─► frontend/templates/ (Jinja2 HTML templates)
```

## Nguyên tắc code

1. **KHÔNG hardcode data** — tất cả providers, roles, menus, settings đều từ DB
2. **KHÔNG defensive null check vô nghĩa** — `get_db()` luôn trả về DatabaseManager, không cần `if not db`
3. **KHÔNG hardcode credentials** — tất cả secrets qua `.env`
4. **KHÔNG commit `.env`** — chỉ commit `.env.example`
5. **KHÔNG dùng f-string SQL** — luôn dùng parameterized query
6. **KHÔNG silent except:pass** — luôn log error với `logger.warning/error`
7. **Controller layer = thin wrapper** — chỉ gọi `get_db().method()`, không có business logic
8. **Single source of truth** — menu registry ở `frontend/menu_registry.py`
9. **KHÔNG xóa whitelist biển số** mà không backup
10. **KHÔNG thay đổi `LINE_Y_RATIO`** mà không test lại TripwireTracker
11. **KHÔNG sửa Dockerfile** mà không rebuild: `docker compose up -d --build`
12. **KHÔNG xóa file trong `data/snapshots/`** thủ công — dùng RetentionManager
13. **KHÔNG sửa `core/config.py`** mà không chạy `python3 -m py_compile core/config.py`

## Cấu trúc folder

```
yolov26-nhandien-biensoxe/
├── main.py                    # Entry point backup (legacy)
├── CLAUDE.md                  # File này — CẬP NHẬT khi release tính năng mới
├── init.sql                   # PostgreSQL schema (source of truth)
├── requirements.txt           # Python deps (backend/requirements.txt)
├── docker-compose.yml         # Stack: ai_core (context: .), postgres
│
├── backend/                    # Core logic and services
│   ├── main.py                # Entry point chính cho container & script
│   ├── core/                  # Business logic thuần Python
│   ├── config.py              # Constants + env loading
│   ├── database.py            # PostgreSQL DatabaseManager (connection pool)
│   ├── docker/                # Dockerfiles
│   │   └── Dockerfile.ai_core # Build với context root (.)
│   └── ...
├── frontend/                    # FastAPI UI (Jinja2 + HTMX + Tailwind)
│   ├── app.py                 # FastAPI entry, routing
│   ├── deps.py                # Dependencies, auth, RBAC
│   ├── auth.py                # Login/logout logic
│   ├── menu_registry.py       # Single source of truth cho menu definitions
│   ├── routers/               # 17 route handlers
│   └── templates/             # Jinja2 HTML templates
│       ├── base.html          # Tailwind CDN + HTMX + Alpine
│       ├── layout.html        # Sidebar + header layout
│       ├── login.html         # Login page
│       └── pages/             # 18 page templates
│
├── models/
│   ├── yolo26_plate_people_door.onnx       # YOLOv26 (gốc)
│   └── yolo26_plate_people_door_int8.onnx  # YOLOv26 INT8 (production)
│
├── util/
│   └── ocr_utils.py           # VNPlateOCR (RapidOCR wrapper)
│
└── tests/
    └── test_security.py       # Security self-tests
```

## Biến môi trường quan trọng

```bash
DATABASE_URL=postgresql://camera_user:password@localhost:5432/camera_ai
IMOU_OPEN_APP_ID=
IMOU_OPEN_APP_SECRET=
IMOU_OPEN_DEVICE_ID=
```

## Tính năng đã release

- **Remember Token Login**: token , localStorage + DB, không lưu password
- **Camera Provider Management**: DB-driven, API keys + email lưu trong app_settings
- **Door Provider Management**: 7 nhà cung cấp cửa cuốn (Tuya, Tasmota, Shelly, Sonoff, BroadLink, GPIO, MQTT)
- **Tuya Door Control**: tinytuya local SDK, toggle relay DPS "1"
- **Stranger Detection**: InsightFace, threshold 0.25, cooldown 60s, log vào DB
- **Unknown Plate → Pending**: biển số không trong whitelist tự động vào pending_plates (cooldown 60s)
- **OCR Active Learning**: confidence 0.3-0.7 lưu sample, admin review qua FastAPI webapp "OCR Review"
- **RBAC Menu System**: menu_registry.py → DB roles → allowed_menus
- **Plate Detection Labels**: chỉ dùng màu (xanh=whitelist, vàng=unknown), không text [OK]/[LA]
- **Login UI**: vertically centered, Enter submit, no duplicate form render
- **Header 50px**: custom header trong layout.html
- **Mobile Responsive**: CSS breakpoints 768px + 480px cho tất cả trang
- **No Fee Feature**: không tính phí gửi xe — chỉ xem camera + nhận diện
- **Multi-site Management**: bảng `sites` thay thế `camera_zones`, global site selector trong sidebar, filter dashboard/cameras theo site
- **Mobile WebRTC Camera View**: TURN server config (env + app_settings), facingMode environment cho camera sau, `?mobile=1` ẩn tab RTSP, responsive CSS cho video player
- **Dynamic Provider API Keys**: mỗi nhà cung cấp camera có bộ key kết nối riêng (PROVIDER_DEFAULT_KEYS), UI tự render dynamic inputs
- **Frontend**: FastAPI + Jinja2 + HTMX + Tailwind CSS (frontend/, port 8000)
- **Auto-Discovery Pipeline**: ARP scan (zero-cost) + MAC OUI vendor ID + ONVIF unicast probe + auto-register. Two-speed: fast (60s ARP) + slow (300s full ONVIF/Dahua/Cloud)
- **Circuit Breaker cho Grabber**: exponential backoff (5s→300s), 3 states (CLOSED→OPEN→HALF_OPEN), health events ghi camera_status_log
- **RTSP URL Auto-Refresh**: grabber tự đọc URL mới từ DB mỗi lần reconnect, xử lý IP thay đổi tự động
- **Auto-Register Cameras**: setting `AUTO_REGISTER_NEW_CAMERAS`, nút toggle trên UI, camera mới tự thêm vào DB
- **Door Auto-Close**: cửa cuốn tự đóng khi không còn người — YOLO person detect + CentroidTracker + TripwireTracker + countdown timer (configurable), Telegram notify khi đóng
- **Camera Health Monitoring**: real-time status indicator (online/offline) trên trang Camera, Telegram alert khi camera offline (circuit breaker opens), API `/cameras/health` trả event log
- **Security**: xóa hardcode Telegram token khỏi source code, secrets chỉ qua `.env`, config qua DB `app_settings`
- **Landing (giao diện user chính) — Tailwind**: toàn bộ site public THÉP SƠN LÂM viết lại bằng Tailwind CSS + Alpine.js (bỏ `landing.css`/`landing.js` cũ). Design tokens `tsl-*` + font Outfit + `.tsl-btn*` khai báo trong `frontend/static/css/input.css` (Tailwind v4 `@theme`/`@layer`). Trang: `/` (home), `/gioi-thieu`, `/du-an` (tổng quan nhà xưởng — hub), `/nha-xuong` (Trung Hà), `/nha-xuong-co-tiet` (Cổ Tiết), `/dich-vu` (build-to-suit), `/tin-tuc` + `/tin-tuc/{slug}`, `/lien-he` (trang liên hệ độc lập, GET; POST `/lien-he` xử lý form). Nav "Nhà xưởng" trỏ hub `/du-an`. Slider dự án + gallery + lightbox chạy bằng Alpine (không còn `window.__projects__`/landing.js). Đã **bỏ** spoke `/xe-cau` + `/kiem-dinh-can`. Vẫn DB-driven (projects/news/inquiries) + i18n VI/中文 + auth menu. **Sau khi sửa landing templates phải rebuild CSS**: `cd frontend && npm run build` (cập nhật `static/css/output.css`).

## Schema DB (init.sql)

| Bảng | Mô tả |
|---|---|
| `roles` | RBAC roles + allowed_menus |
| `users` | Users + remember_token + site_ids |
| `sites` | Địa điểm (bãi xe vật lý) + total_slots |
| `cameras` | Camera config + site_ids (multi-site) |
| `plate_events` | Sự kiện nhận diện biển số |
| `vehicle_sessions` | Phiên đỗ xe |
| `plate_whitelist` | Whitelist biển số |
| `pending_plates` | Biển số lạ chờ duyệt |
| `app_settings` | Key-value settings |
| `camera_status_log` | Camera health |
| `legal_hold` | File protection |
| `monthly_reports` | Báo cáo tổng hợp |
| `telegram_bots` | Multi-bot config |
| `cameras_asset` | CMDB camera assets |
| `camera_providers` | Nhà cung cấp camera |
| `door_providers` | Nhà cung cấp cửa cuốn |
| `rolling_doors` | Cửa cuốn: relay config, camera_id, auto_close settings |
| `stranger_logs` | Face stranger detection |
| `ocr_training_samples` | OCR active learning samples |
| `kyc_documents` | CCCD/selfie/face cho KYC |

## Thông tin kỹ thuật

- **Python**: 3.10.x (production), 3.11.x (dev/test)
- **Model**: `backend/models/yolo26_plate_people_door_int8.onnx` (YOLOv26 INT8)
- **Webapp**: http://host:8000
- **Face detection**: InsightFace buffalo_sc, similarity threshold 0.25
- **Stranger cooldown**: 60s per camera
- **Pending plate cooldown**: 60s per plate
- **OCR sample range**: confidence 0.3-0.7
- **Remember token**: , `secrets.token_urlsafe(32)`
- **Retention**: 30 ngày, legal hold không xóa
- **Door control**: Tuya local (tinytuya), Tasmota, HTTP, GPIO, Hunonic Cloud (MQTT)
- **Door auto-close**: CentroidTracker (IoU) + TripwireTracker, default 300s delay, configurable per door
- **YOLO model classes**: 0=plate, 1=person, 2=door (`yolo26_plate_people_door_int8.onnx`)
- **Circuit breaker**: 5 fails → OPEN 120s → HALF_OPEN test, backoff 5s→300s
- **Camera health**: grabber ghi stream_up/stream_down/crashed vào camera_status_log, Telegram alert on offline
- **Env vars**: chỉ cấu hình hạ tầng cần thiết như DATABASE_URL; session và Telegram không dùng fallback env.
- **Discovery intervals**: ARP 60s (fast), ONVIF+Dahua 300s (slow), configurable via app_settings
