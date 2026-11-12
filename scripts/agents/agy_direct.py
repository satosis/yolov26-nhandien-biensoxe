"""Chạy Antigravity (agy) trực tiếp cho một yêu cầu tự do — dùng làm fallback khi Claude hết hạn.

Khác agy_exec.py (chạy theo file plan có sẵn), bản này nhận yêu cầu qua stdin và chạy agy thẳng,
rồi in câu trả lời của agy ra stdout. Hai kênh gọi nó khi Claude hết quota/hết hạn: nút chat trong
trang quản trị (frontend/modules/claude_code/runner.py) và nhóm Zalo (scripts/zalo_bridge/ai_commands.js).

    echo "yêu cầu..." | python scripts/agents/agy_direct.py --full

Yêu cầu đi qua stdin và agy được gọi không qua shell, nên nội dung không lọt vào dòng lệnh.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_AGY = Path.home() / ".gemini" / "bin" / ("agy.exe" if os.name == "nt" else "agy")

SAFETY = """Thư mục dự án đang có các phiên khác sửa code song song. Chỉ làm đúng yêu cầu dưới đây:
- KHÔNG git checkout/restore/stash/reset, KHÔNG xóa/hoàn tác/sắp xếp lại code ngoài phạm vi yêu cầu
  (không bỏ import/hàm "có vẻ thừa"). Chỉ thêm/sửa đúng chỗ cần.
- Không commit, không push, không cài thêm thư viện trừ khi yêu cầu nêu rõ.
- Không sửa dữ liệu thật trong DB camera_ai.
Yêu cầu:"""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252, không in được tiếng Việt
    sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=os.environ.get("AGY_MODEL", "gemini-3.8-flash-high"))
    parser.add_argument("--full", action="store_true", help="cho agy chạy mọi lệnh không hỏi")
    parser.add_argument("--timeout", type=int, default=1800, help="giây, mặc định 1800")
    args = parser.parse_args()

    request = sys.stdin.read().strip()
    if not request:
        print("Không có yêu cầu trên stdin.", file=sys.stderr)
        return 2
    prompt = f"{SAFETY}\n{request}"

    command = [os.environ.get("AGY_BIN", str(DEFAULT_AGY)), "-p", prompt,
               "--output-format", "json", "--model", args.model]
    command += ["--dangerously-skip-permissions"] if args.full else ["--mode", "accept-edits"]

    try:
        result = subprocess.run(command, cwd=PROJECT_DIR, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"agy quá {args.timeout}s, đã dừng.", file=sys.stderr)
        return 3
    except OSError as error:
        print(f"Không chạy được agy: {error}", file=sys.stderr)
        return 4

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write((result.stderr or result.stdout or f"agy thoát mã {result.returncode}")[-1500:])
        return 1

    answer = str(data.get("response", "")).strip()
    if answer:
        print(answer)
    return 0 if data.get("status") == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
