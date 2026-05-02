import os
import time
import shutil
import requests
import threading
import psutil
from backend.core.config import TOKEN, CHAT_IMPORTANT, CHAT_REGULAR, FACES_DIR, normalize_plate
from backend.core.database import DatabaseManager

_ALLOWED_USERS_RAW = os.environ.get("TELEGRAM_ALLOWED_USERS", "")

# Helper to get dynamic Telegram config from DB with fallback to .env
def get_telegram_config():
    """Fetch global telegram settings from DB app_settings table."""
    db = DatabaseManager()
    token = db.get_app_setting("TELEGRAM_TOKEN", TOKEN)
    chat_important = db.get_app_setting("TELEGRAM_CHAT_IMPORTANT", CHAT_IMPORTANT)
    chat_regular = db.get_app_setting("TELEGRAM_CHAT_REGULAR", CHAT_REGULAR)
    allowed_users_raw = db.get_app_setting("TELEGRAM_ALLOWED_USERS", _ALLOWED_USERS_RAW)
    
    allowed_users = set(
        int(uid.strip()) for uid in allowed_users_raw.split(",") if uid.strip().isdigit()
    )
    return {
        "token": token,
        "chat_important": chat_important,
        "chat_regular": chat_regular,
        "allowed_users": allowed_users
    }


def notify_telegram(message, important=False):
    """Gửi thông báo qua Telegram cho TẤT CẢ các bot trong DB, fallback về config mặc định."""
    prefix = "🚨 [QUAN TRỌNG] " if important else "ℹ️ [THÔNG BÁO] "
    sent_via_db = False
    
    db = DatabaseManager()
    bots = db.get_telegram_bots()
    
    if bots:
        for bot in bots:
            chat_id = bot.get('chat_id')
            if not chat_id:
                continue
            url = f"https://api.telegram.org/bot{bot['token']}/sendMessage"
            try:
                requests.post(url, json={"chat_id": chat_id, "text": prefix + message}, timeout=5)
                sent_via_db = True
            except:
                pass

    # Fallback to single static config from DB/backend.core.config
    if not sent_via_db:
        cfg_dyn = get_telegram_config()
        chat_id = cfg_dyn["chat_important"] if important else cfg_dyn["chat_regular"]
        token = cfg_dyn["token"]
        if not chat_id or not token:
            return
            
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": prefix + message}, timeout=5)


def notify_telegram_video(video_path, caption="", important=False):
    """Gửi video (.mp4) qua Telegram bot."""
    if not os.path.exists(video_path):
        return
        
    prefix = "🚨 [VIDEO CẢNH BÁO] " if important else "📹 [VIDEO GHI HÌNH] "
    sent_via_db = False
    
    db = DatabaseManager()
    bots = db.get_telegram_bots()
    
    if bots:
        for bot in bots:
            chat_id = bot.get('chat_id')
            if not chat_id:
                continue
            url = f"https://api.telegram.org/bot{bot['token']}/sendVideo"
            try:
                with open(video_path, 'rb') as video:
                    requests.post(url, files={'video': video}, data={'chat_id': chat_id, 'caption': prefix + caption}, timeout=30)
                sent_via_db = True
            except Exception as e:
                print(f"Telegram Video (DB) Error: {e}")

    # Fallback to static config
    if not sent_via_db:
        cfg_dyn = get_telegram_config()
        chat_id = cfg_dyn["chat_important"] if important else cfg_dyn["chat_regular"]
        token = cfg_dyn["token"]
        if not chat_id or not token:
            return
            
        url = f"https://api.telegram.org/bot{token}/sendVideo"
        try:
            with open(video_path, 'rb') as video:
                requests.post(url, files={'video': video}, data={'chat_id': chat_id, 'caption': prefix + caption}, timeout=30)
        except Exception as e:
            print(f"Telegram Video (Static) Error: {e}")


def handle_telegram_command(text, chat_id, user_id, db, load_faces_fn):
    """Xử lý lệnh từ Telegram."""
    parts = text.strip().split()
    if not parts:
        return

    cmd = parts[0].lower()

    # Lệnh mở/đóng cửa
    if cmd == "/open":
        print(f"Telegram CMD: OPEN from {user_id}")
        notify_telegram(f"Đã gửi lệnh MỞ cửa theo yêu cầu của {user_id}")
        return

    # Lệnh duyệt biển số
    if cmd in ["/staff", "/reject", "/mine"]:
        if len(parts) < 2:
            notify_telegram(f"Lỗi: Thiếu biển số. VD: {cmd} 29A12345")
            return

        plate_raw = parts[1]
        plate_norm = normalize_plate(plate_raw)

        if cmd == "/mine":
            if db.upsert_vehicle_whitelist(plate_norm, "mine", str(user_id)):
                db.update_pending_status(plate_norm, "approved_mine", str(user_id))
                notify_telegram(f"✅ Đã thêm {plate_norm} vào danh sách CỦA TÔI.")
            else:
                notify_telegram(f"⚠️ Lỗi khi thêm {plate_norm}.")
        elif cmd == "/staff":
            if db.upsert_vehicle_whitelist(plate_norm, "staff", str(user_id)):
                db.update_pending_status(plate_norm, "approved_staff", str(user_id))
                notify_telegram(f"✅ Đã thêm {plate_norm} vào danh sách NHÂN VIÊN.")
            else:
                notify_telegram(f"⚠️ Lỗi khi thêm {plate_norm}.")

        elif cmd == "/reject":
            db.update_pending_status(plate_norm, "rejected", str(user_id))
            notify_telegram(f"🚫 Đã từ chối biển số {plate_norm}.")

    # Lệnh xem các biển số đang chờ duyệt
    if cmd == "/pending":
        pending_plates = db.get_pending_plates()
        if pending_plates:
            msg = "Các biển số đang chờ duyệt:\n"
            for plate_norm, plate_raw, first_seen_utc in pending_plates:
                msg += f"- `{plate_norm}` (raw: {plate_raw}, từ: {first_seen_utc})\n"
            notify_telegram(msg)
        else:
            notify_telegram("Không có biển số nào đang chờ duyệt.")

    # Lệnh duyệt khuôn mặt
    if cmd == "/staff_face":
        if len(parts) < 3:
            notify_telegram("Lỗi cú pháp: /staff_face [ID_TAM] [TEN_NHAN_VIEN]")
            return

        face_id = parts[1]
        staff_name = parts[2].replace(" ", "_")

        temp_path = f"./config/faces/temp/{face_id}.jpg"
        target_path = f"./config/faces/{staff_name}.jpg"

        if os.path.exists(temp_path):
            os.rename(temp_path, target_path)
            notify_telegram(f"✅ Đã thêm nhân viên: {staff_name}")
            load_faces_fn()
        else:
            notify_telegram(f"⚠️ Không tìm thấy ảnh tạm: {face_id}")

    # Lệnh dọn dẹp
    if cmd == "/cleanup":
        if len(parts) < 2:
            notify_telegram("Lỗi cú pháp: /cleanup [faces|active_learning|db]")
            return

        target = parts[1].lower()
        if target == "faces":
            if os.path.exists(FACES_DIR):
                shutil.rmtree(FACES_DIR)
                os.makedirs(FACES_DIR)
                notify_telegram("✅ Đã dọn dẹp thư mục khuôn mặt.")
                load_faces_fn()
            else:
                notify_telegram("Thư mục khuôn mặt không tồn tại.")
        elif target == "active_learning":
            al_dir = "./data/active_learning"
            if os.path.exists(al_dir):
                shutil.rmtree(al_dir)
                os.makedirs(al_dir)
                notify_telegram("✅ Đã dọn dẹp thư mục active learning.")
            else:
                notify_telegram("Thư mục active learning không tồn tại.")
        elif target == "db":
            db_path = db.path
            if os.path.exists(db_path):
                os.remove(db_path)
                db.init_db()
                notify_telegram("✅ Đã dọn dẹp cơ sở dữ liệu.")
            else:
                notify_telegram("Tệp cơ sở dữ liệu không tồn tại.")
        else:
            notify_telegram("⚠️ Mục tiêu dọn dẹp không hợp lệ. Chọn: faces, active_learning, db.")


def telegram_polling_loop(db, load_faces_fn):
    """Vòng lặp nhận tin nhắn từ Telegram."""
    cfg_dyn = get_telegram_config()
    token = cfg_dyn["token"]
    if not token:
        return

    last_update_id = 0
    print("🤖 Telegram Bot listening...")

    while True:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        params = {"offset": last_update_id + 1, "timeout": 30}
        resp = requests.get(url, params=params, timeout=40)

        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                for update in data.get("result", []):
                    last_update_id = update["update_id"]

                    if "message" in update and "text" in update["message"]:
                        msg = update["message"]
                        text = msg["text"]
                        chat_id_msg = msg["chat"]["id"]
                        user_id = msg["from"]["id"]

                        if str(chat_id_msg) in [str(cfg_dyn["chat_important"]), str(cfg_dyn["chat_regular"])]:
                            if cfg_dyn["allowed_users"] and user_id not in cfg_dyn["allowed_users"]:
                                continue
                            handle_telegram_command(text, chat_id_msg, user_id, db, load_faces_fn)
                            # Forward non-command messages to agents (REMOVED)
                            pass

        time.sleep(1)


def telegram_bot_handler(db, get_cpu_temp_fn, get_state_fn):
    """Handler Telegram bot với lệnh /stats, /sys, /cleanup, /mine, /staff, /reject."""
    last_update_id = 0
    while True:
        cfg_dyn = get_telegram_config()
        token = cfg_dyn["token"]
        if not token:
            time.sleep(10)
            continue
            
        url = f"https://api.telegram.org/bot{token}/getUpdates?offset={last_update_id + 1}&timeout=10"
        r = requests.get(url, timeout=15).json()
        if r.get("ok"):
            for update in r["result"]:
                last_update_id = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id")
                user = msg.get("from", {})
                user_label = user.get("username") or str(user.get("id") or "unknown")
                user_id = user.get("id")

                if TELEGRAM_ALLOWED_USERS and user_id not in TELEGRAM_ALLOWED_USERS:
                    continue

                if not text or not text.startswith("/"):
                    continue

                parts = text.strip().split(maxsplit=1)
                cmd = parts[0].split("@")[0].lower()
                plate_raw = parts[1] if len(parts) > 1 else ""
                plate_norm = normalize_plate(plate_raw)

                truck_count, person_count = get_state_fn()

                if cmd == "/stats":
                    rows = db.get_stats()
                    stat_text = "📊 Thống kê hôm nay:\n"
                    for row in rows:
                        stat_text += f"- {row[1]}: {row[0]} lần\n"
                    stat_text += f"\nHiện tại: {truck_count} xe, {person_count} người."

                    temp = get_cpu_temp_fn()
                    temp_str = f"{temp:.1f}°C" if temp else "N/A"
                    disk = psutil.disk_usage('/')
                    stat_text += f"\n\n🖥 Hệ thống:\n- Temp: {temp_str}\n- Disk: {disk.percent}%"

                    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                                  json={"chat_id": chat_id, "text": stat_text})
                    continue

                if cmd == "/sys":
                    temp = get_cpu_temp_fn()
                    temp_str = f"{temp:.1f}°C" if temp else "N/A"
                    notify_telegram(f"🖥 Hệ thống: {temp_str} | Disk: {psutil.disk_usage('/').percent}%")
                    continue

                if cmd == "/cleanup":
                    al_dir = "./data/active_learning"
                    if os.path.exists(al_dir):
                        shutil.rmtree(al_dir)
                        os.makedirs(al_dir)
                    notify_telegram("✅ Đã dọn dẹp bộ nhớ đệm (Active Learning).")
                    continue

                if cmd in {"/mine", "/staff", "/reject"} and not plate_norm:
                    requests.post(
                        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": "Thiếu biển số. Ví dụ: /mine 51A12345"}
                    )
                    continue

                if cmd == "/mine":
                    if db.upsert_vehicle_whitelist(plate_norm, "mine", user_label):
                        db.update_pending_status(plate_norm, "approved_mine", user_label)
                        reply = f"✅ Đã thêm {plate_norm} vào whitelist (mine)."
                    else:
                        reply = f"⚠️ Không thể cập nhật whitelist cho {plate_norm}."
                    requests.post(
                        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": reply}
                    )
                elif cmd == "/staff":
                    if db.upsert_vehicle_whitelist(plate_norm, "staff", user_label):
                        db.update_pending_status(plate_norm, "approved_staff", user_label)
                        reply = f"✅ Đã thêm {plate_norm} vào whitelist (staff)."
                    else:
                        reply = f"⚠️ Không thể cập nhật whitelist cho {plate_norm}."
                    requests.post(
                        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": reply}
                    )
                elif cmd == "/reject":
                    db.update_pending_status(plate_norm, "rejected", user_label)
                    requests.post(
                        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": f"✅ Đã từ chối {plate_norm}."}
                    )
        time.sleep(2)


def start_telegram_threads(db, load_faces_fn, get_cpu_temp_fn, get_state_fn):
    """Khởi chạy tất cả telegram threads."""
    threading.Thread(target=telegram_polling_loop, args=(db, load_faces_fn), daemon=True).start()
    threading.Thread(target=telegram_bot_handler, args=(db, get_cpu_temp_fn, get_state_fn), daemon=True).start()
