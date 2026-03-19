from streamlit_app.models.detector_model import get_db

class VehicleController:
    """Controller for vehicle whitelist logic."""

    @staticmethod
    def get_sites():
        return get_db().get_sites()

    @staticmethod
    def get_whitelist_vehicles(site_id=None):
        return get_db().get_whitelist_vehicles(site_id)

    @staticmethod
    def upsert_vehicle_whitelist(plate_norm, owner_name, added_by, site_id=None, note=None):
        return get_db().upsert_vehicle_whitelist(plate_norm, owner_name, added_by, site_id, note)

    @staticmethod
    def update_vehicle_whitelist(vehicle_id, plate_norm, owner_name, site_id=None, note=None):
        return get_db().update_vehicle_whitelist(vehicle_id, plate_norm, owner_name, site_id, note)

    @staticmethod
    def delete_whitelist_vehicle(plate_norm, site_id=None):
        return get_db().delete_whitelist_vehicle(plate_norm, site_id)

    @staticmethod
    def is_plate_whitelisted(plate_number, site_id=None):
        return get_db().is_plate_whitelisted(plate_number, site_id)

vehicle_controller = VehicleController()
