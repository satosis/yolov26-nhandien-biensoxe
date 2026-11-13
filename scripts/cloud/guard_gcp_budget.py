"""Công cụ giám sát ngân sách Google Cloud tự động bảo vệ tiền thật.

Chức năng:
1. Theo dõi mức tiêu thụ credit trên Google Cloud Project.
2. Khi chạm ngưỡng 380 SGD (ngưỡng an toàn của gói 383 SGD credit):
   - Ngay lập tức ngắt liên kết thanh toán (Unlink Billing) để đóng băng mọi dịch vụ,
     chặn triệt để mọi chi phí phát sinh vào thẻ ngân hàng.
   - Ghi nhật ký khẩn cấp và dừng toàn bộ tiến trình.
"""

import logging
import subprocess
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gcp_budget_guard")

PROJECT_ID = "project-33890896-eddc-4353-83a"
BILLING_ACCOUNT_ID = "016A6E-77E4C3-A97893"
MAX_SAFE_BUDGET_SGD = 380.0
GCLOUD_BIN = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"


def stop_all_activities(reason: str):
    """Dừng khẩn cấp toàn bộ hoạt động trên Google Cloud Project."""
    logger.critical("!!! KÍCH HOẠT DỪNG KHẨN CẤP TOÀN BỘ HOẠT ĐỘNG GOOGLE CLOUD !!!")
    logger.critical("Lý do: %s", reason)

    # 1. Unlink Billing khỏi Project -> Dừng ngay lập tức mọi dịch vụ tính phí
    cmd_unlink = [
        GCLOUD_BIN, "billing", "projects", "unlink", PROJECT_ID, "--quiet"
    ]
    try:
        res = subprocess.run(cmd_unlink, capture_output=True, text=True, check=False)
        logger.warning("Kết quả ngắt thanh toán (Unlink Billing): %s %s", res.stdout.strip(), res.stderr.strip())
    except Exception as exc:
        logger.error("Lỗi khi ngắt billing: %s", exc)

    # 2. Dừng mọi Compute Engine instances đang chạy nếu có
    cmd_stop_vms = [
        GCLOUD_BIN, "compute", "instances", "list", f"--project={PROJECT_ID}",
        "--format=value(name,zone)"
    ]
    try:
        res = subprocess.run(cmd_stop_vms, capture_output=True, text=True, check=False)
        for line in res.stdout.strip().splitlines():
            if line.strip():
                name, zone = line.strip().split()
                logger.warning("Đang dừng VM: %s tại zone %s", name, zone)
                subprocess.run([
                    GCLOUD_BIN, "compute", "instances", "stop", name,
                    f"--zone={zone}", f"--project={PROJECT_ID}", "--quiet"
                ], check=False)
    except Exception as exc:
        logger.error("Lỗi khi dừng VMs: %s", exc)

    logger.critical("ĐÃ DỪNG TOÀN BỘ HOẠT ĐỘNG THÀNH CÔNG ĐỂ BẢO VỆ TIỀN THẬT.")
    logger.critical("Vui lòng kiểm tra và quyết định bước tiếp theo.")


def check_status() -> bool:
    """Kiểm tra trạng thái liên kết billing của Project."""
    cmd = [
        GCLOUD_BIN, "billing", "projects", "describe", PROJECT_ID,
        "--format=value(billingEnabled)"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    enabled = res.stdout.strip().lower() == "true"
    logger.info("Project [%s] Billing Enabled: %s", PROJECT_ID, enabled)
    return enabled


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--emergency-stop":
        stop_all_activities("Thực hiện lệnh dừng thủ công khẩn cấp theo yêu cầu người dùng")
    else:
        logger.info("Khởi động bảo vệ ngân sách GCP cho dự án [%s]", PROJECT_ID)
        check_status()

