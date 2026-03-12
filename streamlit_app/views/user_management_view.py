import streamlit as st
import pandas as pd
from streamlit_app.models.detector_model import get_db, get_password_hash # I need to make sure get_password_hash is available in the model

def user_management_view():
    st.subheader("👥 Quản lý người dùng (Admin Only)")
    
    db = get_db()
    if not db:
        st.error("Database connection failed")
        return

    # Tabs for different operations
    tab_list, tab_add, tab_rbac = st.tabs(["📋 Danh sách người dùng", "➕ Thêm người dùng mới", "🛡️ Phân quyền truy cập"])

    with tab_list:
        users = db.get_all_users()
        if users:
            df = pd.DataFrame(users)
            # Reorder columns
            df = df[['id', 'username', 'role', 'created_at']]
            st.dataframe(df, use_container_width=True)

            st.divider()
            st.markdown("### ✏️ Sửa / Xóa")
            col_sel, col_act = st.columns([1, 2])
            
            with col_sel:
                selected_username = st.selectbox("Chọn người dùng", [u['username'] for u in users if u['username'] != 'admin'])
            
            if selected_username:
                user_to_edit = next(u for u in users if u['username'] == selected_username)
                roles = db.get_roles()
                role_names = [r['role_name'] for r in roles]
                
                with col_act:
                    with st.form(f"edit_user_{user_to_edit['id']}"):
                        new_username = st.text_input("Username", value=user_to_edit['username'])
                        new_role = st.selectbox("Role", role_names, index=role_names.index(user_to_edit['role']))
                        new_pass = st.text_input("Mật khẩu mới (để trống nếu không đổi)", type="password")
                        
                        btn_update, btn_delete = st.columns(2)
                        if btn_update.form_submit_button("💾 Cập nhật", use_container_width=True):
                            hashed = None
                            if new_pass:
                                hashed = get_password_hash(new_pass)
                            
                            if db.update_user(user_to_edit['id'], new_username, new_role, hashed):
                                st.success(f"Đã cập nhật {new_username}")
                                st.rerun()
                            else:
                                st.error("Lỗi khi cập nhật")
                        
                        if btn_delete.form_submit_button("🗑️ Xóa", use_container_width=True, type="secondary"):
                            if db.delete_user(user_to_edit['id']):
                                st.warning(f"Đã xóa {new_username}")
                                st.rerun()
                            else:
                                st.error("Lỗi khi xóa")
        else:
            st.info("Chưa có người dùng nào (ngoài admin)")

    with tab_add:
        st.markdown("### 🆕 Tạo tài khoản mới")
        with st.form("add_user_form"):
            user_name = st.text_input("Username")
            user_pass = st.text_input("Password", type="password")
            roles = db.get_roles()
            role_names = [r['role_name'] for r in roles]
            user_role = st.selectbox("Role", role_names, index=role_names.index('Security') if 'Security' in role_names else 0)
            
            if st.form_submit_button("🚀 Tạo người dùng", use_container_width=True):
                if user_name and user_pass:
                    hashed = get_password_hash(user_pass)
                    if db.create_user(user_name, hashed, user_role):
                        st.success(f"Đã tạo thành công người dùng {user_name}")
                        st.rerun()
                    else:
                        st.error("Lỗi: Người dùng này (username) đã tồn tại trong hệ thống!")
                else:
                    st.warning("Vui lòng nhập đầy đủ thông tin.")

    with tab_rbac:
        st.markdown("### 🛡 Cấu hình Menu theo Role")
        st.info("Ghi chú: Admin luôn có toàn quyền (*). Những role khác chỉ nhìn thấy các menu được gán ở đây.")
        
        roles = db.get_roles()
        all_menus = {
            "detection": "🔍 Nhận diện",
            "cameras": "📹 Quản lý Camera",
            "dori": "📐 DORI",
            "asset": "🗂 Asset",
            "sla": "📊 SLA",
            "health": "❤️ Health",
            "config": "⚙️ Config",
            "telegram": "🤖 Quản lý Telegram",
            "users": "👥 Phân quyền"
        }
        
        for r in roles:
            with st.expander(f"Phân quyền cho: {r['role_name']} - {r['description']}"):
                if r['role_name'] == 'Admin':
                    st.success("Admin luôn có đầy đủ quyền truy cập (All Menus: *)")
                    continue
                
                with st.form(f"rbac_form_{r['id']}"):
                    current_menus = r.get('allowed_menus', '').split(',')
                    selected_for_role = []
                    
                    st.write("Đánh dấu các menu hiển thị:")
                    # Display checkboxes in columns
                    cols = st.columns(4)
                    for idx, (m_key, m_label) in enumerate(all_menus.items()):
                        # Bỏ qua quyền users nếu không muốn ai ngoài admin tự cấp quyền
                        if m_key == 'users' and r['role_name'] != 'Supervisor':
                             pass # Supervisor gets it by default in our SQL script for testing, but let's allow it generally for RBAC demo
                        
                        col = cols[idx % 4]
                        is_checked = (m_key in current_menus) or (current_menus == ['*'])
                        if col.checkbox(m_label, value=is_checked, key=f"chk_{r['id']}_{m_key}"):
                            selected_for_role.append(m_key)
                    
                    if st.form_submit_button("💾 Lưu Cấu Hình Quyền", type="primary"):
                        new_allowed = ",".join(selected_for_role)
                        if selected_for_role == list(all_menus.keys()):
                            new_allowed = "*"
                        elif not selected_for_role:
                            new_allowed = "none" # Avoid empty string confusion
                            
                        if db.update_role_permissions(r['id'], new_allowed):
                            st.success(f"Đã cập nhật quyền cho {r['role_name']}")
                            st.rerun()
                        else:
                            st.error("Lỗi lưu cấu hình RBAC")
