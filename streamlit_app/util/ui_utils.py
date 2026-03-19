import streamlit as st
import os
import logging

def load_css(file_name="global_styles.css"):
    """
    Load CSS from the assets/css directory and inject it into the Streamlit app.
    """
    # Get the directory of the current file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Construct the path to the CSS file
    css_file_path = os.path.join(current_dir, "..", "assets", "css", file_name)
    
    try:
        with open(css_file_path, "r", encoding="utf-8") as f:
            css_content = f.read()
            st.markdown(f"<style>{css_content}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        logging.warning(f"CSS file not found at {css_file_path}")
    except Exception as e:
        st.error(f"Error loading CSS: {str(e)}")
