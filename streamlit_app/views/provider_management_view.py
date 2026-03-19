import streamlit as st
import html as html_mod
from streamlit_app.controllers.provider_controller import provider_controller
from streamlit_app.controllers.camera_controller import camera_controller
from streamlit_app.utils.toast import show_toast
from streamlit_app.util.ui_components import render_page_header, render_table_header, render_table_row

PROVIDER_DEFAULT_KEYS = {
    "imou":      ["app_id", "app_secret", "device_id"],
    "hikvision": ["username", "password", "host", "app_key", "app_secret"],
    "dahua":     ["username", "password", "host", "client_id", "client_secret"],
    "reolink":   ["username", "password", "host"],
    "tapo":      ["username", "password", "host", "onvif_port"],
    "axis":      ["username", "password", "host", "api_key"],
    "uniview":   ["username", "password", "host", "app_id", "app_secret"],
    "hanwha":    ["username", "password", "host", "api_key"],
    "bosch":     ["username", "password", "host", "sdk_token"],
    "rtsp":      ["username", "password", "rtsp_url"],
    "onvif":     ["username", "password", "host", "onvif_port"],
}


def _mask(val) -> str:
    v = str(val or "")
    return ("●" * 4 + v[-4:]) if len(v) > 4 else "●" * 4


def _toggle_html(is_on: bool) -> str:
    bg = "#22c55e" if is_on else "#64748b"
    pos = "calc(100% - 20px)" if is_on else "2px"
    label, color = ("Bật", "#22c55e") if is_on else ("Tắt", "#94a3b8")
    return (
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<div style="width:40px;height:22px;border-radius:11px;background:{bg};position:relative;">'
        f'<div style="width:18px;height:18px;border-radius:50%;background:#fff;'
        f'position:absolute;top:2px;left:{pos};transition:left .2s;"></div></div>'
        f'<span style="font-size:11px;font-weight:500;color:{color};">{label}</span></div>'
    )


def _render_table(providers: list):
    col_specs = [
        {'label': 'TÊN NCC', 'width': '180px'},
        {'label': 'PHƯƠNG THỨC', 'width': '160px'},
        {'label': 'API KEYS', 'width': '100%'},
        {'label': 'KÍCH HOẠT', 'width': '100px'},
        {'label': 'HÀNH ĐỘNG', 'width': '120px'}
    ]
    st.markdown(render_table_header(col_specs), unsafe_allow_html=True)

    for p in providers:
        pk = p["provider_key"]
        name = html_mod.escape(p["name"])
        notes = html_mod.escape(p.get("notes") or "")
        active = p.get("is_active", True)
        saved = provider_controller.get_api_keys(pk)
        keys_html = ""
        if saved:
            for k, v in list(saved.items())[:2]:
                if k == "email":
                    continue
                keys_html += (
                    f'<span style="font-family:JetBrains Mono,monospace;font-size:11px;'
                    f'color:#64748b;">{html_mod.escape(k)}: {_mask(v)}</span><br>'
                )
        else:
            keys_html = '<span style="color:#64748b;font-size:11px;">Chưa cấu hình</span>'

        toggle = _toggle_html(active)
        
        name_col = f"<div class='text-[13px] font-bold text-slate-50'>{name}</div><div class='text-[11px] text-slate-500'>{html_mod.escape(pk)}</div>"
        notes_col = f"<span class='text-xs text-slate-400'>{notes}</span>"
        actions_col = f"<div class='flex gap-2' data-pk='{html_mod.escape(pk)}'></div>"

        col_data = [
            {'content': name_col, 'width': '180px'},
            {'content': notes_col, 'width': '160px'},
            {'content': keys_html, 'width': '100%'},
            {'content': toggle, 'width': '100px'},
            {'content': actions_col, 'width': '120px'}
        ]

        st.markdown(render_table_row(col_data, is_active=active), unsafe_allow_html=True)


def _provider_form(existing=None, context="edit"):
    key_prefix = f"{context}_{existing['provider_key']}" if existing else f"{context}_new"
    with st.form(f"pf_{key_prefix}"):
        c1, c2 = st.columns(2)
        pkey = c1.text_input("Provider Key (*)", value=existing["provider_key"] if existing else "",
                             placeholder="hikvision", disabled=bool(existing))
        pname = c2.text_input("Tên (*)", value=existing["name"] if existing else "", placeholder="Hikvision")

        c3, c4 = st.columns(2)
        api_base = c3.text_input("API Base URL", value=existing.get("api_base_url") or "" if existing else "")
        api_doc = c4.text_input("Tài liệu API", value=existing.get("api_doc_url") or "" if existing else "")

        rtsp_tmpl = st.text_input("RTSP Template", value=existing.get("rtsp_template") or "" if existing else "",
                                  placeholder="rtsp://{user}:{pass}@{ip}:554/stream")

        c5, c6, c7 = st.columns(3)
        onvif_p = c5.number_input("ONVIF Port", value=int(existing.get("onvif_port") or 80) if existing else 80,
                                  min_value=1, max_value=65535)
        rtsp_p = c6.number_input("RTSP Port", value=int(existing.get("default_rtsp_port") or 554) if existing else 554,
                                 min_value=1, max_value=65535)
        http_p = c7.number_input("HTTP Port", value=int(existing.get("default_http_port") or 80) if existing else 80,
                                 min_value=1, max_value=65535)

        method_val = st.text_input("Phương thức kết nối",
                                   value=existing.get("notes") or "" if existing else "",
                                   placeholder="VD: Open API + RTSP, ISAPI + RTSP...",
                                   key=f"method_{key_prefix}")

        # --- Dynamic API Keys ---
        saved_api = provider_controller.get_api_keys(existing["provider_key"]) if existing else {}
        pk = existing["provider_key"] if existing else pkey
        default_keys = PROVIDER_DEFAULT_KEYS.get(pk, [])
        all_keys = list(default_keys)
        for k in saved_api:
            if k != "email" and k not in all_keys:
                all_keys.append(k)

        api_vals = {}
        if all_keys:
            st.caption("🔑 API Keys")
        for fk in all_keys:
            fv = saved_api.get(fk, "")
            is_secret = any(s in fk for s in ("secret", "password", "key"))
            c_val, c_del = st.columns([5, 1])
            api_val = c_val.text_input(fk, value=fv,
                                       type="password" if is_secret else "default",
                                       key=f"apif_{key_prefix}_{fk}")
            is_deleted = c_del.checkbox("🗑", key=f"apif_del_{key_prefix}_{fk}")
            api_vals[fk] = "" if is_deleted else api_val

        c_nk, c_nv = st.columns([2, 3])
        new_key = c_nk.text_input("Tên key mới", placeholder="app_id, token...", key=f"new_api_key_{key_prefix}")
        new_val = c_nv.text_input("Giá trị", key=f"new_api_val_{key_prefix}")
        if new_key.strip():
            api_vals[new_key.strip().lower().replace(" ", "_")] = new_val

        email_val = st.text_input("📧 Email tài khoản",
                                  value=saved_api.get("email", ""),
                                  key=f"apif_{key_prefix}_email")
        api_vals["email"] = email_val

        active_val = st.toggle("Kích hoạt", value=existing.get("is_active", True) if existing else True,
                               key=f"active_{key_prefix}")

        b1, b2 = st.columns(2)
        submitted = b1.form_submit_button("💾 Lưu", type="primary", use_container_width=True)
        cancelled = b2.form_submit_button("Hủy", use_container_width=True)

    if submitted:
        key = existing["provider_key"] if existing else pkey.strip().lower().replace(" ", "_")
        if not key or not pname.strip():
            st.error("Vui lòng điền Provider Key và Tên.")
        else:
            ok = provider_controller.upsert(key, pname.strip(), api_base or None, api_doc or None,
                                            rtsp_tmpl or None, int(onvif_p), int(rtsp_p), int(http_p),
                                            method_val or None, None, active_val)
            if ok:
                if api_vals and any(str(v).strip() for v in api_vals.values()):
                    camera_controller.update_provider_api_keys(key, api_vals)
                st.session_state.pop("edit_provider_key", None)
                st.session_state.pop("show_add_provider", None)
                show_toast(f"Đã lưu: {pname}", type="success")
                st.rerun()
            else:
                st.error("Lỗi khi lưu Database.")
    if cancelled:
        st.session_state.pop("edit_provider_key", None)
        st.session_state.pop("show_add_provider", None)
        st.rerun()


def _confirm_delete(pk: str, name: str):
    st.warning(f"Xác nhận xóa **{name}**? Hành động này không thể hoàn tác.")
    c1, c2 = st.columns(2)
    if c1.button("🗑 Xóa", key=f"yd_{pk}", type="primary", use_container_width=True):
        provider_controller.delete(pk)
        st.session_state.pop("delete_provider_key", None)
        show_toast(f"Đã xóa {name}", type="success")
        st.rerun()
    if c2.button("Hủy", key=f"nd_{pk}", use_container_width=True):
        st.session_state.pop("delete_provider_key", None)
        st.rerun()


def provider_management_view():
    # --- Header ---
    col_h, col_btn = st.columns([4, 1])
    with col_h:
        render_page_header("Nhà cung cấp Camera", "🏭")
    if col_btn.button("➕ Thêm NCC", type="primary", use_container_width=True):
        st.session_state["show_add_provider"] = True
        st.session_state.pop("edit_provider_key", None)

    # --- Add form ---
    if st.session_state.get("show_add_provider"):
        with st.expander("➕ Thêm nhà cung cấp mới", expanded=True):
            _provider_form(existing=None, context="add")

    # --- Delete confirmation ---
    del_key = st.session_state.get("delete_provider_key")
    if del_key:
        p_del = provider_controller.get(del_key)
        if p_del:
            _confirm_delete(del_key, p_del["name"])

    # --- Table ---
    providers = provider_controller.get_all()
    if not providers:
        st.info("Chưa có nhà cung cấp nào.")
        return

    _render_table(providers)

    # --- Action buttons per provider (Streamlit buttons below table) ---
    edit_key = st.session_state.get("edit_provider_key")
    cols = st.columns(len(providers))
    for i, p in enumerate(providers):
        pk = p["provider_key"]
        with cols[i]:
            is_editing = edit_key == pk
            bc1, bc2, bc3 = st.columns(3)
            if bc1.button("✏️" if not is_editing else "✕", key=f"ed_{pk}",
                          help="Sửa" if not is_editing else "Đóng"):
                if is_editing:
                    st.session_state.pop("edit_provider_key", None)
                else:
                    st.session_state["edit_provider_key"] = pk
                    st.session_state.pop("show_add_provider", None)
                st.rerun()
            if bc2.button("🗑", key=f"dl_{pk}", help="Xóa"):
                st.session_state["delete_provider_key"] = pk
                st.rerun()
            if bc3.button("🔄" if p.get("is_active") else "⏸", key=f"tg_{pk}",
                          help="Tắt" if p.get("is_active") else "Bật"):
                provider_controller.upsert(
                    pk, p["name"], p.get("api_base_url"), p.get("api_doc_url"),
                    p.get("rtsp_template"), p.get("onvif_port", 80),
                    p.get("default_rtsp_port", 554), p.get("default_http_port", 80),
                    p.get("notes"), p.get("logo_url"), not p.get("is_active", True)
                )
                show_toast(f"{'Tắt' if p.get('is_active') else 'Bật'} {p['name']}", type="success")
                st.rerun()

    # --- Edit form ---
    if edit_key:
        p_edit = provider_controller.get(edit_key)
        if p_edit:
            with st.expander(f"✏️ Sửa: {p_edit['name']}", expanded=True):
                _provider_form(existing=p_edit, context="edit")
