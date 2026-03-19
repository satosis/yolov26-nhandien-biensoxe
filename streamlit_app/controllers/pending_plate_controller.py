from streamlit_app.models.detector_model import get_db


class PendingPlateController:
    @staticmethod
    def get_pending():
        return get_db().get_pending_plates()

    @staticmethod
    def approve(plate_norm, confirmed_by):
        db = get_db()
        db.update_pending_status(plate_norm, "approved", confirmed_by)
        db.upsert_vehicle_whitelist(plate_norm, "", confirmed_by)

    @staticmethod
    def reject(plate_norm, confirmed_by):
        return get_db().update_pending_status(plate_norm, "rejected", confirmed_by)


pending_plate_controller = PendingPlateController()
