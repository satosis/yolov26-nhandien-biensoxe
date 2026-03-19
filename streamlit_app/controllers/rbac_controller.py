from streamlit_app.models.detector_model import get_db

class RBACController:
    @staticmethod
    def get_roles():
        return get_db().get_roles()

    @staticmethod
    def update_role_permissions(role_id, allowed_menus):
        return get_db().update_role_permissions(role_id, allowed_menus)

    @staticmethod
    def create_role(role_name, description=""):
        return get_db().create_role(role_name, description)

    @staticmethod
    def delete_role(role_id):
        return get_db().delete_role(role_id)

rbac_controller = RBACController()
