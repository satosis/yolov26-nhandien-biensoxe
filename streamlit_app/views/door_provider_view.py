import streamlit as st
import html as html_mod
from streamlit_app.controllers.door_provider_controller import door_provider_controller
from streamlit_app.utils.toast import show_toast

_CONNECTION_TYPES = ["gpio", "http", "tuya_local", "mqtt"]

_CONN_BADGE = {
    "gpio": ("🔌 GPIO", "#6366f1"),
    "http": ("🌐 HTTP API", "#0ea5e9"),
    "tuya_local": ("☁️ Local SDK", "#f59e0b"),
    "mqtt": ("📡 MQTT", "#10b981"),
}


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


def _render_table(providers):
    hdr = (
        '<div class="prov-table">'
        '<div class="prov-hdr">'
        '<span style="width:160px">TÊN NCC</span>'
        '<span style="width:120px">LOẠI</span>'
        '<span style="flex:1">MÔ TẢ</span>'
        '<span style="width:100px">KÍCH HOẠT</span>'
        '<span style="width:120px">HÀNH ĐỘNG</span>'
        '</div>'
    )
    rows = ""
    for p in providers:
        pk = p["provider_key"]
        name = html_mod.escape(p["name"])
        ct = p.get("connection_type") or ""
        icon_label, badge_color = _CONN_BADGE.get(ct, ("🔧 " + ct, "#94a3b8"))
        notes = html_mod.escape(p.get("notes") or "—")
        active = p.get("is_active", True)
        toggle = _toggle_html(active)
        badge = (
            f'<span style="display:inline-block;padding:3px 10px;border-radius:100px;'
            f'font-size:11px;font-weight:600;color:{badge_color};'
            f'background:{badge_color}20;">{icon_label}</span>'
        )
        rows += (
            f'<div class="prov-row">'
            f'<div style="width:160px"><div style="font-size:13px;font-weight:600;color:#f8fafc;">{name}</div>'
            f'<div style="font-size:11px;color:#64748b;">{html_mod.escape(pk)}</div></div>'
            f'<div style="width:120px">{badge}</div>'
            f'<span style="flex:1;font-size:12px;color:#94a3b8;">{notes}</span>'
            f'<div style="width:100px">{toggle}</div>'
            f'<div style="width:120px"></div>'
            f'</div>'
        )
    st.markdown(hdr + rows + "</div>", unsafe_allow_html=True)


def _door_provider_form(existing=None, context="edit"):
    key_prefix = f"{context}_{existing['provider_key']}" if existing else f"{context}_new"
    with st.form(f"dpf_{key_prefix}"):
        c1, c2 = st.columns(2)
        pkey = c1.text_input("Provider Key (*)", value=existing["provider_key"] if existing else "",
                             placeholder="tuya_local", disabled=bool(existing))
        pname = c2.text_input("Tên (*)", value=existing["name"] if existing else "",
                              placeholder="Tuya Local")

        conn_type_val = existing.get("connection_type") or _CONNECTION_TYPES[0] if existing else _CONNECTION_TYPES[0]
        conn_idx = _CONNECTION_TYPES.index(conn_type_val) if conn_type_val in _CONNECTION_TYPES else 0
        conn_type = st.selectbox("Loại kết nối", _CONNECTION_TYPES, index=conn_idx,
                                 key=f"ct_{key_prefix}")

        api_doc = st.text_input("Tài liệu API (URL)",
                                value=existing.get("api_doc_url") or "" if existing else "",
                                placeholder="https://developer.tuya.com/...")
        config_tmpl = st.text_area("Config Template (JSON)",
                                   value=existing.get("config_template") or "" if existing else "",
                                   height=80, placeholder='{"device_id": "", "local_key": ""}')
        notes_val = st.text_area("Ghi chú", value=existing.get("notes") or "" if existing else "", height=60)
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
            ok = door_provider_controller.upsert(
                key, pname.strip(), conn_type,
                api_doc or None, config_tmpl or None, notes_val or None, active_val
            )
            if ok:
                st.session_state.pop("edit_door_provider_key", None)
                st.session_state.pop("show_add_door_provider", None)
                show_toast(f"Đã lưu: {pname}", type="success")
                st.rerun()
            else:
                st.error("Lỗi khi lưu Database.")
    if cancelled:
        st.session_state.pop("edit_door_provider_key", None)
        st.session_state.pop("show_add_door_provider", None)
        st.rerun()


def _confirm_delete(pk, name):
    st.warning(f"Xác nhận xóa **{name}**? Hành động này không thể hoàn tác.")
    c1, c2 = st.columns(2)
    if c1.button("🗑 Xóa", key=f"yd_dp_{pk}", type="primary", use_container_width=True):
        door_provider_controller.delete(pk)
        st.session_state.pop("delete_door_provider_key", None)
        show_toast(f"Đã xóa {name}", type="success")
        st.rerun()
    if c2.button("Hủy", key=f"nd_dp_{pk}", use_container_width=True):
        st.session_state.pop("delete_door_provider_key", None)
        st.rerun()


def door_provider_view():
    # --- Header ---
    col_h, col_btn = st.columns([4, 1])
    col_h.markdown("### 🚪 Nhà cung cấp Cửa cuốn")
    if col_btn.button("➕ Thêm NCC", type="primary", use_container_width=True):
        st.session_state["show_add_door_provider"] = True
        st.session_state.pop("edit_door_provider_key", None)

    # --- Add form ---
    if st.session_state.get("show_add_door_provider"):
        with st.expander("➕ Thêm nhà cung cấp mới", expanded=True):
            _door_provider_form(existing=None, context="add")

    # --- Delete confirmation ---
    del_key = st.session_state.get("delete_door_provider_key")
    if del_key:
        p_del = door_provider_controller.get(del_key)
        if p_del:
            _confirm_delete(del_key, p_del["name"])

    # --- Table ---
    providers = door_provider_controller.get_all()
    if not providers:
        st.info("Chưa có nhà cung cấp nào.")
        return

    _render_table(providers)

    # --- Action buttons ---
    edit_key = st.session_state.get("edit_door_provider_key")
    cols = st.columns(len(providers))
    for i, p in enumerate(providers):
        pk = p["provider_key"]
        with cols[i]:
            is_editing = edit_key == pk
            bc1, bc2, bc3 = st.columns(3)
            if bc1.button("✏️" if not is_editing else "✕", key=f"ed_dp_{pk}",
                          help="Sửa" if not is_editing else "Đóng"):
                if is_editing:
                    st.session_state.pop("edit_door_provider_key", None)
                else:
                    st.session_state["edit_door_provider_key"] = pk
                    st.session_state.pop("show_add_door_provider", None)
                st.rerun()
            if bc2.button("🗑", key=f"dl_dp_{pk}", help="Xóa"):
                st.session_state["delete_door_provider_key"] = pk
                st.rerun()
            if bc3.button("🔄" if p.get("is_active") else "⏸", key=f"tg_dp_{pk}",
                          help="Tắt" if p.get("is_active") else "Bật"):
                door_provider_controller.upsert(
                    pk, p["name"], p.get("connection_type"),
                    p.get("api_doc_url"), p.get("config_template"),
                    p.get("notes"), not p.get("is_active", True)
                )
                show_toast(f"{'Tắt' if p.get('is_active') else 'Bật'} {p['name']}", type="success")
                st.rerun()

    # --- Edit form ---
    if edit_key:
        p_edit = door_provider_controller.get(edit_key)
        if p_edit:
            with st.expander(f"✏️ Sửa: {p_edit['name']}", expanded=True):
                _door_provider_form(existing=p_edit, context="edit")
