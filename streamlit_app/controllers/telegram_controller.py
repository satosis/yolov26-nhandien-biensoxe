from streamlit_app.models.detector_model import get_db

class TelegramController:
    @staticmethod
    def get_telegram_bots():
        return get_db().get_telegram_bots()

    @staticmethod
    def upsert_telegram_bot(bot_name, token, chat_id):
        return get_db().upsert_telegram_bot(bot_name, token, chat_id)

    @staticmethod
    def delete_telegram_bot(bot_name):
        return get_db().delete_telegram_bot(bot_name)

telegram_controller = TelegramController()
