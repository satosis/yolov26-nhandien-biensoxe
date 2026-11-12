import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import multiprocessing as mp
import ssl
import certifi

ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

from dotenv import load_dotenv
load_dotenv()

from backend.services import config as cfg
cfg.setup_logging("backend.log")

from backend.services.orchestrator import CameraOrchestrator
from backend.services import facebook_autopost, facebook_inbox, zalo_bridge, google_ads_ops, marketing_ops, pending_sales_rollover

logger = logging.getLogger("main")

if __name__ == "__main__":
    mp.set_start_method('spawn', force=True)

    logger.info("================================================")
    logger.info("   Backend Orchestrator is starting...         ")
    logger.info("   Connect to UI at: http://localhost:8001    ")
    logger.info("================================================")

    # Zalo bridge (nhận tin nhóm + nhóm lệnh AI) chạy và dừng cùng backend.
    bridge_supervisor = zalo_bridge.start_supervisor()
    # AI viết sẵn bài Facebook cho lịch đăng; bài chỉ lên Page sau khi được duyệt trong admin.
    autopost_scheduler = facebook_autopost.start_scheduler()
    # Bình luận và tin nhắn mới vào Page: ghi Yêu cầu tư vấn và AI trả lời.
    inbox_scheduler = facebook_inbox.start_scheduler()
    ads_scheduler = google_ads_ops.start_scheduler()
    marketing_scheduler = marketing_ops.start_scheduler()
    pending_sales_scheduler = pending_sales_rollover.start_scheduler()
    orch = CameraOrchestrator()
    try:
        orch.run()
    finally:
        bridge_supervisor.set()
        autopost_scheduler.set()
        inbox_scheduler.set()
        ads_scheduler.set()
        marketing_scheduler.set()
        pending_sales_scheduler.set()
        zalo_bridge.stop()
        orch.cleanup()
        logger.info("Backend stopped.")
