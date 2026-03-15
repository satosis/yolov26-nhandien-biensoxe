import streamlit as st
import subprocess
import os
import signal
import psutil
import time
import sys

def get_hpc_status():
    """Kiểm tra xem engine HPC có đang chạy không."""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline')
            if cmdline and 'streamlit_app.engine.main' in ' '.join(cmdline):
                return proc.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None

def auto_start_hpc():
    """Tự động khởi động Engine nếu chưa chạy."""
    pid = get_hpc_status()
    if not pid:
        try:
            # Chạy script hpc main dưới dạng process ngầm từ vị trí mới
            if not os.path.exists("data"):
                os.makedirs("data")
            log_file = open("data/hpc_engine.log", "a")
            subprocess.Popen(
                [sys.executable, "-m", "streamlit_app.engine.main"],
                stdout=log_file,
                stderr=log_file,
                cwd=os.getcwd(),
                start_new_session=True
            )
            return True
        except Exception:
            return False
    return True

def hpc_engine_view():
    st.header("⚡ AI Engine Hiệu Năng Cao (HPC)")
    st.info("Chế độ này tối ưu 100% tài nguyên Orange Pi 4 Pro để xử lý camera với tốc độ cao nhất.")

    pid = get_hpc_status()
    
    col1, col2 = st.columns(2)
    
    if pid:
        st.success(f"✅ AI Engine đang chạy (PID: {pid})")
        if st.button("🔴 Dừng AI Engine", type="primary", width='stretch'):
            try:
                os.kill(pid, signal.SIGTERM)
                st.toast("Đang dừng Engine...")
                time.sleep(2)
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi khi dừng: {e}")
    else:
        st.warning("⚪ AI Engine đang dừng")
        if st.button("🚀 Khởi động AI Engine", type="primary", width='stretch'):
            try:
                # Chạy script hpc main dưới dạng process ngầm từ vị trí mới
                log_file = open("data/hpc_engine.log", "a")
                subprocess.Popen(
                    [sys.executable, "-m", "streamlit_app.engine.main"],
                    stdout=log_file,
                    stderr=log_file,
                    cwd=os.getcwd(),
                    start_new_session=True
                )
                st.toast("Đang khởi động Engine...")
                time.sleep(3)
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi khi khởi động: {e}")

    st.divider()
    
    with st.expander("📝 Xem Log hệ thống (Thời gian thực)"):
        if os.path.exists("data/hpc_engine.log"):
            with open("data/hpc_engine.log", "r") as f:
                # Đọc 50 dòng cuối
                lines = f.readlines()[-50:]
                st.code("".join(lines), language="text")
        else:
            st.write("Chưa có log.")

    st.divider()
    st.markdown("""
    ### 🛠 Thông tin kỹ thuật:
    - **Hardware:** Orange Pi 4 Pro (RK3399)
    - **Cores:** 6 Cores (2 Big + 4 Little)
    - **Optimization:** CPU Performance Governor + ZRAM + Multiprocessing
    - **Status:** Tự động ghi dữ liệu vào PostgreSQL để hiển thị ở các tab khác.
    """)
