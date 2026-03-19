import os


def get_ice_servers() -> list:
    """Build ICE server list from env vars (priority) or app_settings (fallback)."""
    from streamlit_app.models.detector_model import get_db

    db = get_db()
    turn_url = os.environ.get("WEBRTC_TURN_URL") or db.get_app_setting("WEBRTC_TURN_URL") or ""
    turn_user = os.environ.get("WEBRTC_TURN_USERNAME") or db.get_app_setting("WEBRTC_TURN_USERNAME") or ""
    turn_pass = os.environ.get("WEBRTC_TURN_PASSWORD") or db.get_app_setting("WEBRTC_TURN_PASSWORD") or ""

    servers = [{"urls": ["stun:stun.l.google.com:19302"]}]
    if turn_url.strip():
        turn_entry = {"urls": [turn_url.strip()]}
        if turn_user.strip():
            turn_entry["username"] = turn_user.strip()
            turn_entry["credential"] = turn_pass
        servers.append(turn_entry)
    return servers
