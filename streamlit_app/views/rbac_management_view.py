import streamlit as st
from streamlit_app.models.detector_model import get_db

def rbac_management_view():
    st.subheader("🛡️ Quản lý Phân quyền (RBAC)")
    
    db = get_db()
    if not db:
        st.error("Database connection failed")
        return

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
        "users": "👥 Người dùng",
        "rbac": "🛡️ Phân quyền"
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
                    col = cols[idx % 4]
                    is_checked = (m_key in current_menus) or (current_menus == ['*'])
                    if col.checkbox(m_label, value=is_checked, key=f"chk_{r['id']}_{m_key}"):
                        selected_for_role.append(m_key)
                
                if st.form_submit_button("💾 Lưu Cấu Hình Quyền", type="primary"):
                    new_allowed = ",".join(selected_for_role)
                    if selected_for_role == list(all_menus.keys()):
                        new_allowed = "*"
                    elif not selected_for_role:
                        new_allowed = "none"
                        
                    if db.update_role_permissions(r['id'], new_allowed):
                        st.success(f"Đã cập nhật quyền cho {r['role_name']}")
                        st.rerun()
                    else:
                        st.error("Lỗi lưu cấu hình RBAC")
