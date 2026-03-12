import streamlit as st
import requests
from streamlit_app.models.detector_model import get_db

def check_bot_status(token):
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=2).json()
        return r.get("ok", False), r.get("result", {}).get("username", "Unknown")
    except:
        return False, "N/A"

def set_custom_css():
    st.markdown("""
        <style>
        .bot-card { border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin-bottom: 20px; background-color: #ffffff; }
        .bot-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 15px; }
        .bot-title-group { display: flex; align-items: center; gap: 15px; }
        .bot-icon { background: #f1f5f9; color: #3b82f6; width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; font-family: monospace; }
        .bot-name { font-weight: bold; font-size: 1.1rem; color: #1e293b; margin: 0; }
        .bot-meta { color: #64748b; font-size: 0.85rem; margin: 0; }
        .bot-active-toggle { color: #10b981; font-size: 0.9rem; font-weight: 500; display: flex; align-items: center; gap: 8px; }
        </style>
    """, unsafe_allow_html=True)

def multi_telegram_view(bot_config=None):
    set_custom_css()
    db = get_db()
    
    # State management for Modals
    if 'show_add_bot' not in st.session_state:
        st.session_state['show_add_bot'] = False
        
    def open_add_modal(): st.session_state['show_add_bot'] = True
    def close_add_modal(): st.session_state['show_add_bot'] = False

    # Header
    col1, col2 = st.columns([5, 1])
    with col1:
        st.markdown("### Cấu Hình Telegram Bot")
        st.markdown("<p style='color: #64748b; font-size: 0.9rem;'>Quản lý các bot và cài đặt gửi thông báo</p>", unsafe_allow_html=True)
    with col2:
        if not st.session_state['show_add_bot']:
            st.button("➕ Thêm Bot Mới", type="primary", use_container_width=True, on_click=open_add_modal)

    # ADD BOT "MODAL" (Rendered inline at top if active)
    if st.session_state['show_add_bot']:
        with st.container(border=True):
            st.markdown("#### ➕ Thêm Bot Mới\n<span style='color:gray;font-size:14px'>Kết nối bot Telegram với hệ thống</span>", unsafe_allow_html=True)
            with st.form("add_bot_form"):
                n_name = st.text_input("Tên Bot *", placeholder="VD: Staging Bot")
                n_tok = st.text_input("Bot Token *", placeholder="123456789:AABBCcdd...", type="password")
                n_cid = st.text_input("Chat ID *", placeholder="-100123456789")
                
                c_cancel, c_submit = st.columns([8, 2])
                c_cancel.form_submit_button("Hủy", on_click=close_add_modal)
                if c_submit.form_submit_button("Lưu Bot", type="primary"):
                    if n_name and n_tok and n_cid:
                        if db.upsert_telegram_bot(n_name, n_tok, n_cid, ""):
                            st.success("Lưu bot thành công!")
                            close_add_modal()
                            st.rerun()
                        else:
                            st.error("Lỗi khi lưu DB!")
                    else:
                        st.error("Vui lòng điền các trường bắt buộc (*)")

    st.markdown("<br>", unsafe_allow_html=True)
    tab1, tab2 = st.tabs(["🤖 Telegram Bots", "⚙ Cài Đặt"])

    with tab1:
        bots = db.get_telegram_bots()
        if not bots:
            st.info("Hiện hệ thống chưa liên kết Bot Telegram nào. Bấm 'Thêm Bot Mới' để bắt đầu.")
            return

        for idx, bot in enumerate(bots):
            with st.container(border=True):
                # Custom Header using HTML for exact look
                header_html = f"""
                <div class='bot-header'>
                    <div class='bot-title-group'>
                        <div class='bot-icon'>(o)</div>
                        <div>
                            <p class='bot-name'>{bot['bot_name']}</p>
                            <p class='bot-meta'>Tạo ngày {str(bot.get('created_at') or bot.get('updated_at', 'N/A'))[:10]} • Quản lý cảnh báo & hệ thống</p>
                        </div>
                    </div>
                </div>
                """
                st.markdown(header_html, unsafe_allow_html=True)
                
                # Input configuration fields
                c_tok, c_cid = st.columns(2)
                
                # Editing logic inline
                token_val = c_tok.text_input("Bot Token", value=bot['token'], type="password", key=f"tok_{bot['bot_name']}")
                cid_val = c_cid.text_input("Chat ID (Important)", value=bot['chat_id_important'], key=f"cid_{bot['bot_name']}")
                
                st.caption(f"Trạng thái Database (Cập nhật lúc: {str(bot.get('updated_at', 'N/A'))[:19]})")
                
                st.divider()
                
                # Action Buttons
                c_btn1, c_btn2, c_btn3, c_spacer = st.columns([2.5, 2, 2, 5])
                
                if c_btn1.button("💬 Gửi tin nhắn thử", key=f"test_{bot['bot_name']}"):
                    if cid_val and token_val:
                        res, user = check_bot_status(token_val)
                        if res:
                            api_url = f"https://api.telegram.org/bot{token_val}/sendMessage"
                            send_res = requests.post(api_url, json={"chat_id": cid_val, "text": f"✅ Test Msg từ Hệ thống qua bot @{user}"}).json()
                            if send_res.get("ok"):
                                st.success("Gửi tin nhắn thử thành công!")
                            else:
                                st.error(f"Telegram chặn: {send_res.get('description')}")
                        else:
                            st.error("Bot Token không hợp lệ hoặc Telegram không phản hồi.")
                    else:
                        st.warning("Cần cấu hình đủ Token & Chat ID!")
                        
                if c_btn2.button("📝 Lưu sửa bot", key=f"save_{bot['bot_name']}"):
                    db.upsert_telegram_bot(bot['bot_name'], token_val, cid_val, bot['chat_id_normal'])
                    st.success("Đã lưu thông tin mới!")
                    st.rerun()
                    
                if c_btn3.button("🗑 Xóa bot", key=f"del_{bot['bot_name']}", type="primary"):
                    db.delete_telegram_bot(bot['bot_name'])
                    st.warning(f"Đã xóa bot {bot['bot_name']}")
                    st.rerun()

    with tab2:
        st.info("Tính năng cài đặt thông báo nâng cao đang được phát triển...")
