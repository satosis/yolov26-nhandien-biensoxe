import streamlit as st
import streamlit.components.v1 as components
import os
import base64
import json
import time
from streamlit_app.controllers.user_controller import user_controller

COOKIE_PREFIX = "parking_camera_"
SESSION_TIMEOUT_MINUTES = 60
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 900  # 15 minutes

# --- localStorage Helpers (token ONLY, never password) ---

def _read_localstorage():
    """Read remember token from localStorage via query param bridge."""
    if st.session_state.get('_ls_loaded'):
        return

    ls_token = st.query_params.get('_ls_token')
    if ls_token:
        st.session_state['_saved_token'] = ls_token
        st.session_state['_ls_loaded'] = True
        if '_ls_token' in st.query_params:
            del st.query_params['_ls_token']
            st.rerun()


def _inject_ls_reader():
    """Inject JS to read localStorage token and reload with it as query param."""
    if st.session_state.get('_ls_loaded') or st.session_state.get('_ls_reader_injected'):
        return

    # If we already tried a token and it was invalid, clear it first
    if st.session_state.get('_token_invalid'):
        st.session_state.pop('_token_invalid', None)
        st.session_state['_ls_reader_injected'] = True
        clear_localstorage(reload=False)
        return

    js_code = """
    <script>
    (function() {
        const prefix = "parking_camera_";
        const token = localStorage.getItem(prefix + "remember_token") || "";
        // Clean up legacy password storage
        localStorage.removeItem(prefix + "password");
        if (token) {
            const url = new URL(window.parent.location);
            if (!url.searchParams.has('_ls_token')) {
                url.searchParams.set('_ls_token', token);
                window.parent.history.replaceState({}, '', url);
                window.parent.location.reload();
            }
        }
    })();
    </script>
    """
    components.html(js_code, height=0)
    st.session_state['_ls_reader_injected'] = True


def _save_token_to_localstorage(token: str):
    """Inject JS to save remember token to localStorage. Never stores password."""
    safe_token = json.dumps(token)
    js_code = f"""
    <script>
    (function() {{
        const prefix = "parking_camera_";
        localStorage.setItem(prefix + "remember_token", {safe_token});
        // Ensure no legacy password remains
        localStorage.removeItem(prefix + "password");
    }})();
    </script>
    """
    components.html(js_code, height=0)


def clear_localstorage(reload=True):
    """Inject JS to clear all saved credentials from localStorage."""
    reload_js = "window.parent.location.reload();" if reload else ""
    js_code = f"""
    <script>
    (function() {{
        const prefix = "parking_camera_";
        localStorage.removeItem(prefix + "remember_token");
        localStorage.removeItem(prefix + "saved_username");
        localStorage.removeItem(prefix + "password");
        {f'setTimeout(() => {{ {reload_js} }}, 100);' if reload else ""}
    }})();
    </script>
    """
    components.html(js_code, height=0)


def logout_user():
    """Perform a full logout: clear DB tokens and signal session to clear localStorage."""
    user = st.session_state.get('user')
    if user and 'id' in user:
        user_controller.delete_remember_tokens(user['id'])
    st.query_params.clear()
    st.session_state['logout_pending'] = True
    st.session_state['authenticated'] = False
    st.rerun()


# --- Rate Limiting ---

def _check_rate_limit(username: str) -> bool:
    """Return True if login is allowed, False if locked out."""
    attempts = st.session_state.get('_login_attempts', {})
    info = attempts.get(username)
    if not info:
        return True
    if info['count'] >= MAX_LOGIN_ATTEMPTS:
        elapsed = time.time() - info['last']
        if elapsed < LOCKOUT_SECONDS:
            return False
        # Lockout expired, reset
        attempts.pop(username, None)
        st.session_state['_login_attempts'] = attempts
    return True


def _record_failed_attempt(username: str):
    attempts = st.session_state.setdefault('_login_attempts', {})
    info = attempts.get(username, {'count': 0, 'last': 0})
    info['count'] += 1
    info['last'] = time.time()
    attempts[username] = info
    st.session_state['_login_attempts'] = attempts


def _clear_attempts(username: str):
    attempts = st.session_state.get('_login_attempts', {})
    attempts.pop(username, None)
    st.session_state['_login_attempts'] = attempts


# --- UI ---

def set_bg():
    bg_path = os.path.join(os.path.dirname(__file__), "assets", "login_bg.png")
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
    </style>
    '''
    st.markdown(page_bg_css, unsafe_allow_html=True)


def login_view():
    set_bg()
    st.markdown("""
    <style>
    .block-container { padding-top: 0 !important; }
    section[data-testid="stMain"] > div { display: flex; align-items: center; min-height: 100vh; }
    </style>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        with st.container(border=True):
            st.markdown("<h1>PLATFORM</h1>", unsafe_allow_html=True)
            st.markdown("<p class='subtitle'>Hệ thống quản lý Camera AI chuyên dụng</p>", unsafe_allow_html=True)

            with st.form("login_form"):
                username = st.text_input("Tài khoản", placeholder="Nhập username...")
                password = st.text_input("Mật khẩu", type="password", placeholder="••••••••")
                submitted = st.form_submit_button("ĐĂNG NHẬP", use_container_width=True, type="primary")

            if submitted:
                if not _check_rate_limit(username):
                    remaining = LOCKOUT_SECONDS - (time.time() - st.session_state.get('_login_attempts', {}).get(username, {}).get('last', 0))
                    st.error(f"Tài khoản bị khóa tạm. Thử lại sau {int(remaining)}s.")
                else:
                    user = user_controller.get_user(username)
                    if user and user_controller.verify_password(password, user['hashed_password']):
                        _clear_attempts(username)
                        st.session_state['authenticated'] = True
                        st.session_state['user'] = user
                        st.session_state['_last_activity'] = time.time()

                        # Always remember login token
                        token = user_controller.create_remember_token(user['id'])
                        if token:
                            st.session_state['_pending_save_token'] = token
                        st.rerun()
                    else:
                        _record_failed_attempt(username)
                        attempts_left = MAX_LOGIN_ATTEMPTS - st.session_state.get('_login_attempts', {}).get(username, {}).get('count', 0)
                        if attempts_left > 0:
                            st.error(f"Sai tài khoản hoặc mật khẩu. Còn {attempts_left} lần thử.")
                        else:
                            st.error(f"Tài khoản bị khóa {LOCKOUT_SECONDS // 60} phút.")

            st.markdown('<p style="text-align: center; color: #475569; font-size: 0.7rem; margin-top: 30px;">SECURE LOGON • ENCRYPTED SESSION • v2.2.0</p>', unsafe_allow_html=True)

    _inject_ls_reader()


# --- PLACEHOLDER_CHECK_AUTH ---

def check_auth():
    """Gate function: ensures user is authenticated before proceeding."""
    if st.session_state.get('logout_pending'):
        clear_localstorage()
        st.query_params.clear()
        st.session_state.clear()
        st.session_state['_ls_loaded'] = True
        login_view()
        st.stop()

    if st.session_state.get('authenticated'):
        user = st.session_state.get('user')
        if not user or 'role' not in user:
            st.session_state.clear()
            st.rerun()
        # Session timeout check
        last = st.session_state.get('_last_activity', 0)
        if last and (time.time() - last) > SESSION_TIMEOUT_MINUTES * 60:
            st.session_state.clear()
            st.rerun()
        st.session_state['_last_activity'] = time.time()
        return

    # Try auto-login from remember token
    _read_localstorage()
    saved_token = st.session_state.get('_saved_token', '')
    if saved_token:
        user = user_controller.get_user_by_remember_token(saved_token)
        if user:
            st.session_state['authenticated'] = True
            st.session_state['user'] = user
            st.session_state['_last_activity'] = time.time()
            return
        else:
            st.session_state['_token_invalid'] = True
            st.session_state.pop('_saved_token', None)

    login_view()
    st.stop()


def flush_pending_localstorage():
    """Call this from main app after page render to inject pending localStorage ops."""
    pending_token = st.session_state.pop('_pending_save_token', None)
    if pending_token:
        _save_token_to_localstorage(pending_token)

    if st.session_state.pop('_pending_clear_all', False):
        clear_localstorage()
    elif st.session_state.pop('_pending_clear_token', False):
        clear_localstorage()
