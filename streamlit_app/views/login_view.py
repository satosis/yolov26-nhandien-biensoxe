import streamlit as st
import time
import os
from streamlit_app.models.detector_model import get_db, verify_password
from streamlit_cookies_manager import EncryptedCookieManager


# Use a secure prefix for our cookies
COOKIE_PREFIX = "parking_camera_"
# The library by default sets persistent cookies (typically expiring far in the future).
cookies = EncryptedCookieManager(
    password=os.environ.get("COOKIE_PASSWORD", "super-secret-password-for-cookies"),
)

if not cookies.ready():
    st.stop()

def set_bg():
    bg_path = "/Users/mac/Documents/yolov11-nhandien-biensoxe/streamlit_app/assets/login_bg.png"
    bin_str = ""
    if os.path.exists(bg_path):
        with open(bg_path, "rb") as f:
            bin_str = base64.b64encode(f.read()).decode()
            
    page_bg_css = f'''
    <style>
    .stApp {{
        background: url("data:image/png;base64,{bin_str}");
        background-size: cover;
    }}
    /* Target the container with border=True */
    [data-testid="stVerticalBlockBorderWrapper"] {{
        background: rgba(15, 23, 42, 0.6) !important;
        backdrop-filter: blur(20px) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 24px !important;
        padding: 40px !important;
        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.8) !important;
    }}
    .stButton>button {{
        background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
        color: white;
        border: none;
        padding: 12px;
        border-radius: 12px;
        font-weight: 700;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        height: 3.5rem;
        margin-top: 10px;
    }}
    .stButton>button:hover {{
        transform: translateY(-2px);
        box-shadow: 0 0 20px rgba(99, 102, 241, 0.4);
        border: none;
        color: white;
    }}
    h1 {{
        background: linear-gradient(to right, #f8fafc, #94a3b8);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.5rem;
        font-weight: 900;
        margin-bottom: 0px;
        text-align: center;
    }}
    .subtitle {{
        color: #94a3b8;
        font-size: 1rem;
        margin-bottom: 30px;
        text-align: center;
        font-weight: 500;
    }}
    /* Custom input styling */
    div[data-baseweb="input"] {{
        background-color: rgba(2, 6, 23, 0.4) !important;
        border: 1px solid rgba(255, 255, 255, 0.05) !important;
        border-radius: 12px !important;
    }}
    div[data-baseweb="input"]:focus-within {{
        border-color: #6366f1 !important;
    }}
    label {{
        color: #e2e8f0 !important;
        font-size: 0.9rem !important;
        font-weight: 600 !important;
    }}
    </style>
    '''
    st.markdown(page_bg_css, unsafe_allow_html=True)

import os
import base64

def login_view():
    set_bg()
    
    st.write("##")
    st.write("##")
    
    col1, col2, col3 = st.columns([1, 1.5, 1])
    
    with col2:
        with st.container(border=True):
            st.markdown("<h1>PLATFORM</h1>", unsafe_allow_html=True)
            st.markdown("<p class='subtitle'>Hệ thống quản lý Camera AI chuyên dụng</p>", unsafe_allow_html=True)
            
            last_username = cookies.get(f"{COOKIE_PREFIX}last_user", "")
            last_password = cookies.get(f"{COOKIE_PREFIX}last_password", "")
            
            username = st.text_input("Tài khoản", value=last_username, placeholder="Nhập username...")
            password = st.text_input("Mật khẩu", value=last_password, type="password", placeholder="••••••••")
            
            remember_me = st.checkbox("Ghi nhớ đăng nhập", value=True)
            
            if st.button("TRUY CẬP TRUNG TÂM ĐIỀU KHIỂN"):
                db = get_db()
                if db:
                    user = db.get_user(username)
                    if user and verify_password(password, user['hashed_password']):
                        st.session_state['authenticated'] = True
                        st.session_state['user'] = user
                        
                        if remember_me:
                            cookies[f"{COOKIE_PREFIX}username"] = username
                            cookies[f"{COOKIE_PREFIX}last_user"] = username
                            cookies[f"{COOKIE_PREFIX}last_password"] = password
                            cookies.save()
                            
                        st.success("Đang kết nối...")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("Xác thực không chính xác")
                else:
                    st.error("Lỗi kết nối cơ sở dữ liệu")
            
            st.markdown('<p style="text-align: center; color: #475569; font-size: 0.7rem; margin-top: 30px;">SECURE LOGON • ENCRYPTED SESSION • v2.1.0</p>', unsafe_allow_html=True)

def check_auth():
    # 1. Check if already authenticated in this session
    if 'authenticated' in st.session_state and st.session_state['authenticated']:
        return

    # 2. Try auto-login from cookies if not authenticated and not explicitly logged out
    if not st.session_state.get('explicit_logout', False):
        saved_user = cookies.get(f"{COOKIE_PREFIX}username")
        if saved_user:
            db = get_db()
            if db:
                user = db.get_user(saved_user)
                if user:
                    st.session_state['authenticated'] = True
                    st.session_state['user'] = user
                    return # Auto-logged in successfully


    # 3. If still not authenticated, show login
    login_view()
    st.stop()
    
    # Defensive check for stale session data after schema change
    if 'user' not in st.session_state or 'role' not in st.session_state['user']:
        st.session_state['authenticated'] = False
        st.session_state.clear() # Clear everything to be safe
        st.rerun()
