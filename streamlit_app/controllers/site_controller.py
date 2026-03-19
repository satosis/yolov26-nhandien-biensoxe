from streamlit_app.models.detector_model import get_db


class SiteController:
    @staticmethod
    def get_sites(active_only=False):
        return get_db().get_sites(active_only)

    @staticmethod
    def add_site(site_name, address="", description="", total_slots=0):
        return get_db().add_site(site_name, address, description, total_slots)

    @staticmethod
    def update_site(site_id, site_name, address, description, is_active, total_slots=None):
        return get_db().update_site(site_id, site_name, address, description, is_active, total_slots)

    @staticmethod
    def delete_site(site_id):
        return get_db().delete_site(site_id)


site_controller = SiteController()
