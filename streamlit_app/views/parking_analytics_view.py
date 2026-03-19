import streamlit as st
import pandas as pd
import plotly.express as px
from streamlit_app.controllers.parking_controller import parking_controller

def parking_analytics_view():
    st.title("📊 Phân tích Bãi xe")
    
    history = parking_controller.get_parking_history(limit=500)
    if not history:
        st.info("Chưa có đủ dữ liệu để phân tích.")
        return

    df = pd.DataFrame(history)
    df['entered_at_utc'] = pd.to_datetime(df['entered_at_utc'], errors='coerce', utc=True)
    df = df.dropna(subset=['entered_at_utc'])

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

