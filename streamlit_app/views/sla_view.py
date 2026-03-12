import streamlit as st
import pandas as pd

def sla_view(db):
    st.subheader("📊 SLA Report")
    if db is None:
        st.warning("Không kết nối được DB.")
        return

    sla_days = st.slider("Số ngày hiển thị", 7, 90, 30)
    rows = db.get_sla_daily(days=sla_days)
    if rows:
        df_sla = pd.DataFrame(rows)
        avg_uptime = df_sla["uptime_pct"].mean() if "uptime_pct" in df_sla.columns else 0
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Uptime trung bình", f"{avg_uptime:.2f}%")
        st.divider()
        st.line_chart(df_sla.set_index("report_date")["uptime_pct"] if "uptime_pct" in df_sla.columns else None)
        st.dataframe(df_sla, use_container_width=True, hide_index=True)
    else:
        st.info("Chưa có dữ liệu SLA.")

    st.divider()
    st.subheader("📋 SLA Targets")
    st.table(pd.DataFrame([
        {"Priority": "P1", "Sự cố": "Toàn hệ thống sập", "Acknowledge": "≤ 15 phút", "Resolve": "≤ 4 giờ"},
        {"Priority": "P2", "Sự cố": "Recording server lỗi", "Acknowledge": "≤ 1 giờ", "Resolve": "≤ 8 giờ"},
    ]))
