import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from backend.core.database import DatabaseManager

def seed():
    db = DatabaseManager()
    
    # 1. Camera Providers
    camera_providers = [
        {
            "provider_key": "imou",
            "name": "Imou (Dahua Consumer)",
            "logo_url": "/static/assets/providers/imou.png",
            "api_base_url": "https://open.imoulife.com",
            "api_doc_url": "https://open.imoulife.com/book/index.html",
            "rtsp_template": "rtsp://{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}",
            "notes": "Imou Cloud Platform. Ảnh chính thức từ imoulife.com"
        },
        {
            "provider_key": "hikvision",
            "name": "Hikvision",
            "logo_url": "/static/assets/providers/hikvision.png",
            "api_base_url": "https://open.hikvision.com",
            "api_doc_url": "https://tpp.hikvision.com",
            "rtsp_template": "rtsp://admin:{password}@{ip}:{port}/Streaming/Channels/{channel}01",
            "notes": "Hikvision Security. Ảnh từ Wikimedia/Hikvision"
        },
        {
            "provider_key": "ezviz",
            "name": "Ezviz",
            "logo_url": "/static/assets/providers/ezviz.png",
            "api_base_url": "https://open.ezviz.com",
            "api_doc_url": "https://www.ezviz.com/download/branding-assets",
            "rtsp_template": "rtsp://{password}@{ip}:{port}/h264Preview_01_main",
            "notes": "Ezviz Smart Home. Ảnh chính thức từ ezviz.com"
        },
        {
            "provider_key": "dahua",
            "name": "Dahua (Pro / DSS)",
            "logo_url": "/static/assets/providers/dahua.png",
            "api_base_url": "https://open.dahuasecurity.com",
            "api_doc_url": "https://www.dahuasecurity.com/developer",
            "rtsp_template": "rtsp://{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}",
            "notes": "Dahua Pro SDK. Ảnh từ Wikimedia/Dahua"
        }
    ]
    
    print("Seeding Camera Providers...")
    for p in camera_providers:
        db.upsert_provider(**p)
        print(f" - Upserted {p['name']}")
        
    # 2. Door Providers
    door_providers = [
        {
            "provider_key": "austdoor",
            "name": "Austdoor",
            "logo_url": "/static/assets/providers/austdoor.png",
            "connection_type": "http",
            "notes": "Tập đoàn Austdoor - Cửa cuốn số 1 Việt Nam."
        },
        {
            "provider_key": "mitadoor",
            "name": "Mitadoor",
            "logo_url": "/static/assets/providers/mitadoor.png",
            "connection_type": "http",
            "notes": "Cửa cuốn nhôm khe thoáng công nghệ Đức."
        },
        {
            "provider_key": "titadoor",
            "name": "Titadoor",
            "logo_url": "/static/assets/providers/titadoor.png",
            "connection_type": "http",
            "notes": "Thương hiệu cửa cuốn Titadoor chuyên nghiệp."
        },
        {
            "provider_key": "bossdoor",
            "name": "BossDoor",
            "logo_url": "/static/assets/providers/bossdoor.png",
            "connection_type": "http",
            "notes": "Thương hiệu cửa cuốn cao cấp của Tân Trường Sơn Group."
        }
    ]
    
    print("\nSeeding Door Providers...")
    for p in door_providers:
        db.upsert_door_provider(**p)
        print(f" - Upserted {p['name']}")

if __name__ == "__main__":
    seed()
