import streamlit as st
import requests
import html as html_mod
from streamlit_app.controllers.telegram_controller import telegram_controller

@st.cache_data(ttl=300)
def check_bot_status(token):
    if not token: return False, "N/A"
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=3).json()
        return r.get("ok", False), r.get("result", {}).get("username", "Unknown")
    except Exception:
        return False, "N/A"

def set_custom_css():
    st.markdown("""
        <style>
        .bot-profile-card {
            background-color: rgba(30, 41, 59, 0.4);
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 20px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .bot-profile-title {
            color: #38bdf8;
            font-size: 1.2rem;
            font-weight: bold;
            margin-bottom: 5px;
        }
        .bot-profile-desc {
            color: #94a3b8;
            font-size: 0.85rem;
            margin-bottom: 15px;
        }
        </style>
    """, unsafe_allow_html=True)

def multi_telegram_view(bot_config=None):
    set_custom_css()
    
    if 'show_add_bot' not in st.session_state:
        st.session_state['show_add_bot'] = False
        
    def open_add_modal(): st.session_state['show_add_bot'] = True
    def close_add_modal(): st.session_state['show_add_bot'] = False

    # Header
    col1, col2 = st.columns([5, 1])
    with col1:
        st.markdown("### 🤖 Quản lý Cấu hình Telegram")
        st.markdown("<p style='color: #64748b; font-size: 0.9rem;'>Một Bot có thể dùng cho nhiều Chat ID. Mỗi cấu hình (Profile) sẽ lưu một cặp Bot Token + Chat ID.</p>", unsafe_allow_html=True)
    with col2:
        if not st.session_state['show_add_bot']:
            st.button("➕ Thêm Cấu hình", type="primary", width='stretch', on_click=open_add_modal)

    # ADD PROFILE FORM
    if st.session_state['show_add_bot']:
        with st.container(border=True):
            st.markdown("#### ➕ Thêm Cấu Hình Mới", unsafe_allow_html=True)
            with st.form("add_bot_form"):
                n_name = st.text_input("Tên Cấu Hình (Profile Name) *", placeholder="VD: Bot Nhóm 1")
                n_tok = st.text_input("Bot Token *", placeholder="123456789:AABBCcdd...", type="password")
                n_cid = st.text_input("Chat ID *", placeholder="-100123456789")
                
                if st.form_submit_button("Lưu Cấu Hình", type="primary"):
                    if n_name and n_tok and n_cid:
                        if telegram_controller.upsert_telegram_bot(n_name, n_tok, n_cid):
                            st.success("Lưu cấu hình thành công!")
                            close_add_modal()
                            st.rerun()
                        else:
                            st.error("Lỗi khi lưu Database!")
                    else:
                        st.error("Vui lòng điền đủ Tên, Token và Chat ID.")
                if st.form_submit_button("Hủy"):
                    close_add_modal()
                    st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)
    
    bots = telegram_controller.get_telegram_bots()
    if not bots:
        st.info("Chưa có cấu hình Telegram nào. Bấm 'Thêm Cấu hình' để bắt đầu.")
        return

    for idx, bot in enumerate(bots):
        with st.container(border=True):
            bot_name = bot['bot_name']
            token = bot['token']
            
            # Fetch real bot username to verify token
            is_valid, bot_username = check_bot_status(token)
            status_icon = "🟢" if is_valid else "🔴"
            status_text = f"Đã kết nối (@{bot_username})" if is_valid else "Token không hợp lệ"
            
            st.markdown(f"""
            <div class="bot-profile-card">
                <div class="bot-profile-title">📋 {html_mod.escape(bot_name)}</div>
                <div class="bot-profile-desc">{status_icon} <b>Trạng thái:</b> {status_text} &nbsp;|&nbsp; <b>Cập nhật:</b> {str(bot.get('updated_at') or bot.get('created_at'))[:19]}</div>
            </div>
            """, unsafe_allow_html=True)
            
            with st.expander("Chỉnh sửa cấu hình & Gửi thử", expanded=False):
                with st.form(f"edit_form_{idx}"):
                    c_tok = st.text_input("Bot Token", value=token, type="password")
                    c_cid = st.text_input("Chat ID", value=bot.get('chat_id', ''))
                    
                    if st.form_submit_button("📝 Lưu Thay Đổi"):
                        telegram_controller.upsert_telegram_bot(bot_name, c_tok, c_cid)
                        st.success("Đã cập nhật!")
                        st.rerun()

                st.divider()
                st.write("**Gửi tin nhắn thử (Test Connection)**:")
                
                t1, t2 = st.columns([1, 4])
                if t1.button("📩 Gửi Thử Tin Nhắn", key=f"test_msg_{idx}"):
                    if c_cid and c_tok:
                        res, _ = check_bot_status(c_tok)
                        if res:
                            res_test = requests.post(f"https://api.telegram.org/bot{c_tok}/sendMessage", json={"chat_id": c_cid, "text": f"✅ Test Msg từ cấu hình '{bot_name}'"}).json()
                            if res_test.get("ok"): st.success("Gửi thành công!")
                            else: st.error(f"Lỗi Telegram: {res_test.get('description')}")
                        else:
                            st.error("Token không hợp lệ.")
                    else:
                        st.warning("Cần cấu hình đủ Token & Chat ID!")
                        
                if t2.button("🗑 Xóa Cấu Hình", key=f"del_{idx}", type="primary"):
                    telegram_controller.delete_telegram_bot(bot_name)
                    st.warning(f"Đã xóa cấu hình {bot_name}!")
                    st.rerun()
