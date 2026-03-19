from streamlit_app.models.detector_model import get_db


class DoorProviderController:
    @staticmethod
    def get_all():
        return get_db().get_all_door_providers()

    @staticmethod
    def get(provider_key: str):
        return get_db().get_door_provider(provider_key)

    @staticmethod
    def upsert(provider_key, name, connection_type=None, api_doc_url=None,
               config_template=None, notes=None, is_active=True):
        return get_db().upsert_door_provider(provider_key, name, connection_type, api_doc_url,
                                              config_template, notes, is_active)

    @staticmethod
    def delete(provider_key: str):
        return get_db().delete_door_provider(provider_key)


door_provider_controller = DoorProviderController()
