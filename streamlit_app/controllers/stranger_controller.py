from streamlit_app.models.detector_model import get_db


class StrangerController:
    @staticmethod
    def get_strangers(status=None, limit=50):
        return get_db().get_stranger_logs(status=status, limit=limit)

    @staticmethod
    def update_status(stranger_id, status, converted_user_id=None, note=None):
        return get_db().update_stranger_status(stranger_id, status, converted_user_id, note)

    @staticmethod
    def delete(stranger_id):
        return get_db().delete_stranger_log(stranger_id)

    @staticmethod
    def generate_username(prefix="user"):
        return get_db().generate_unique_username(prefix)

    @staticmethod
    def create_user_from_stranger(stranger_id, full_name, role_name=None, site_ids=None, note=None):
        """Tao user moi tu stranger log, auto-generate username, copy face image."""
        db = get_db()

        # Resolve role: use provided or last available from DB (lowest privilege)
        if not role_name:
            roles = db.get_roles()
            role_name = roles[-1]['role_name'] if roles else None
        if not role_name:
            return False, "Khong tim thay role nao trong DB"

        username = db.generate_unique_username("user")

        # Default password from app_settings, or generate random
        import bcrypt
        import secrets
        default_pass_setting = db.get_app_setting("DEFAULT_USER_PASSWORD")
        raw_pass = default_pass_setting or secrets.token_urlsafe(12)
        hashed_pass = bcrypt.hashpw(raw_pass.encode(), bcrypt.gensalt()).decode()
        ok, msg = db.create_user(username, hashed_pass, role_name, full_name, note, site_ids)
        if not ok:
            return False, msg

        user = db.get_user(username)
        if not user:
            return False, "Tao user thanh cong nhung khong tim thay"

        user_id = user['id']

        # Find stranger image path
        stranger_img = None
        all_strangers = db.get_stranger_logs(limit=500)
        for s in all_strangers:
            if s['id'] == stranger_id:
                stranger_img = s.get('image_path')
                break

        db.update_stranger_status(stranger_id, 'converted', converted_user_id=user_id)

        # Copy face image to config/faces/{user_id}/
        import os
        import shutil
        if stranger_img and os.path.exists(stranger_img):
            face_dir = os.path.join("config", "faces", str(user_id))
            os.makedirs(face_dir, exist_ok=True)
            shutil.copy2(stranger_img, os.path.join(face_dir, "face_01.jpg"))

        return True, f"Da tao user '{username}' (ID: {user_id}). Mat khau mac dinh: {raw_pass}"


stranger_controller = StrangerController()
