import streamlit as st
import pandas as pd
import plotly.express as px
from streamlit_app.models.detector_model import get_db

def parking_analytics_view():
    st.title("📊 Phân tích Bãi xe")
    db = get_db()
    if not db:
        st.error("Lỗi kết nối CSDL")
        return

    history = db.get_parking_history(limit=500)
    if not history:
        st.info("Chưa có đủ dữ liệu để phân tích.")
        return

    df = pd.DataFrame(history)
    
    # 1. Average Parking Duration
    completed_df = df[df['status'] == 'completed'].copy()
    if not completed_df.empty:
        avg_seconds = completed_df['stay_duration'].mean()
        avg_minutes = avg_seconds / 60
        st.metric("⏳ Thời gian đỗ trung bình", f"{avg_minutes:.1f} phút")
        
        # Duration distribution
        fig_dist = px.histogram(completed_df, x="stay_duration", 
                             title="Phân bổ thời gian đỗ (giây)",
                             labels={'stay_duration': 'Thời gian (s)'},
                             color_discrete_sequence=['#6366f1'])
        st.plotly_chart(fig_dist, width='stretch')
    else:
        st.info("Chưa có lượt xe hoàn tất để tính thời gian đỗ.")

    st.divider()

    # 2. Traffic over time (Entries)
    st.subheader("📈 Lưu lượng xe vào")
    df['hour'] = df['entered_at_utc'].dt.hour
    traffic_df = df.groupby('hour').size().reset_index(name='count')
    
    fig_traffic = px.bar(traffic_df, x='hour', y='count',
                        title="Số lượt xe vào theo khung giờ",
                        labels={'hour': 'Giờ trong ngày', 'count': 'Số lượt'},
                        color_discrete_sequence=['#10b981'])
    st.plotly_chart(fig_traffic, width='stretch')

    st.divider()

    # 3. Revenue Overview
    if not completed_df.empty:
        total_revenue = completed_df['fee'].sum()
        st.metric("🪙 Tổng doanh thu dự kiến", f"{total_revenue:,} VND")
        
        # Revenue by zone
        rev_zone = completed_df.groupby('zone_name')['fee'].sum().reset_index()
        fig_rev = px.pie(rev_zone, values='fee', names='zone_name', 
                       title="Doanh thu theo Khu vực",
                       hole=0.4)
        st.plotly_chart(fig_rev, width='stretch')
