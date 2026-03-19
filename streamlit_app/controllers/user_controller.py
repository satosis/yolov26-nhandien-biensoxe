from streamlit_app.models.detector_model import get_db, verify_password

class UserController:
    """Controller for user and personnel management logic."""

    @staticmethod
    def get_user(username):
        return get_db().get_user(username)

    @staticmethod
    def verify_password(provided_password, stored_hashed_password):
        return verify_password(provided_password, stored_hashed_password)

    @staticmethod
    def get_all_users():
        return get_db().get_all_users()

    @staticmethod
    def get_roles():
        return get_db().get_roles()

    @staticmethod
    def create_user(username, hashed_password, role_name, full_name=None, note=None, site_ids=None):
        return get_db().create_user(username, hashed_password, role_name, full_name, note, site_ids)

    @staticmethod
    def update_user(user_id, username, role_name, hashed_password=None, full_name=None, note=None, site_ids=None):
        return get_db().update_user(user_id, username, role_name, hashed_password, full_name, note, site_ids)

    @staticmethod
    def delete_user(user_id):
        return get_db().delete_user(user_id)

    @staticmethod
    def get_person_by_identity(identity):
        return get_db().get_person_by_identity(identity)

    @staticmethod
    def is_person_whitelisted(identity, site_id=None):
        return get_db().is_person_whitelisted(identity, site_id)

    # --- Remember Token ---

    @staticmethod
    def create_remember_token(user_id):
        return get_db().create_remember_token(user_id)

    @staticmethod
    def get_user_by_remember_token(token):
        return get_db().get_user_by_remember_token(token)

    @staticmethod
    def delete_remember_tokens(user_id):
        get_db().delete_remember_tokens(user_id)

user_controller = UserController()
