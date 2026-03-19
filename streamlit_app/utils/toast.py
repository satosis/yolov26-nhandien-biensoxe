import streamlit as st
import time

def show_toast(message: str, type: str = "success"):
    """
    Hiển thị thông báo dạng toastr góc trên bên phải bằng HTML/CSS injection.
    type: "success", "error", "info", "warning"
    """
    if type == "success":
        color = "#10b981"
        icon = "✅"
    elif type == "error":
        color = "#ef4444"
        icon = "❌"
    elif type == "warning":
        color = "#f59e0b"
        icon = "⚠️"
    else:
        color = "#3b82f6"
        icon = "ℹ️"
        
    toast_id = f"custom-toastr-{int(time.time() * 1000)}"
    
    html = f"""
        <style>
            @keyframes slideInFadeOut_{toast_id} {{
                0%   {{ transform: translateX(120%); opacity: 0; }}
                10%  {{ transform: translateX(0); opacity: 1; }}
                80%  {{ transform: translateX(0); opacity: 1; }}
                100% {{ transform: translateX(120%); opacity: 0; display: none; }}
            }}
            #{toast_id} {{
                position: fixed; 
                top: 24px; 
                right: 24px; 
                background-color: {color}; 
                color: white; 
                padding: 16px 20px; 
                border-radius: 6px; 
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05); 
                z-index: 999999; 
                display: flex; 
                align-items: center; 
                gap: 12px; 
                font-weight: 500; 
                font-size: 15px;
                font-family: system-ui, -apple-system, sans-serif; 
                animation: slideInFadeOut_{toast_id} 3s ease-in-out forwards;
                pointer-events: none;
            }}
        </style>
        <div id="{toast_id}">
            <span style="font-size: 18px;">{icon}</span>
            <span>{message}</span>
        </div>
    """
    st.markdown(html, unsafe_allow_html=True)
