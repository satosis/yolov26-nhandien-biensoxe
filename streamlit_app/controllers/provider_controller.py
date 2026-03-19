from streamlit_app.models.detector_model import get_db


class ProviderController:
    @staticmethod
    def get_all():
        return get_db().get_all_providers()

    @staticmethod
    def get(provider_key: str):
        return get_db().get_provider(provider_key)

    @staticmethod
    def upsert(provider_key, name, api_base_url=None, api_doc_url=None,
               rtsp_template=None, onvif_port=80, default_rtsp_port=554,
               default_http_port=80, notes=None, logo_url=None, is_active=True):
        return get_db().upsert_provider(provider_key, name, api_base_url, api_doc_url,
                                        rtsp_template, onvif_port, default_rtsp_port,
                                        default_http_port, notes, logo_url, is_active)

    @staticmethod
    def delete(provider_key: str):
        return get_db().delete_provider(provider_key)

    @staticmethod
    def get_api_keys(provider_key: str):
        return get_db().get_provider_api_keys(provider_key)

    @staticmethod
    def update_api_keys(provider_key: str, data: dict):
        return get_db().update_provider_api_keys(provider_key, data)


provider_controller = ProviderController()
