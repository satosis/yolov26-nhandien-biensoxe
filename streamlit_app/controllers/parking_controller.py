from streamlit_app.models.detector_model import get_db

class ParkingController:
    @staticmethod
    def get_occupancy_stats(site_id=None):
        return get_db().get_occupancy_stats(site_id=site_id)

    @staticmethod
    def get_parking_history(limit=100):
        return get_db().get_parking_history(limit)

parking_controller = ParkingController()
