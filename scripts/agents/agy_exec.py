"""Giao một file plan cho Antigravity CLI (agy) viết code, in bản tóm tắt ngắn cho Claude review.

Claude lập plan (tốn ít token output), Antigravity làm phần viết code (quota riêng), Claude chỉ
đọc tóm tắt + `git diff` thay vì tự viết toàn bộ.

    python scripts/agents/agy_exec.py .ai/plans/sua-loi-x.md
    python scripts/agents/agy_exec.py .ai/plans/sua-loi-x.md --resume <conversation_id> --note "Sửa thêm: ..."

Mặc định agy chỉ được tự sửa file (--mode accept-edits); --full cho chạy cả lệnh không cần hỏi.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
RUNS_DIR = PROJECT_DIR / ".ai" / "runs"
DEFAULT_AGY = Path.home() / ".gemini" / "bin" / ("agy.exe" if os.name == "nt" else "agy")

INSTRUCTIONS = """Bạn là người thực thi. Làm đúng plan trong file {plan} (đọc file đó trước), không mở rộng phạm vi.
- Chỉ sửa các file plan cho phép; giữ nguyên style code xung quanh.
- Không commit, không push, không cài thêm thư viện trừ khi plan yêu cầu.
- Thư mục đang có thay đổi của người khác làm song song: KHÔNG xóa, hoàn tác hay sắp xếp lại code ngoài phạm vi plan
  (không git checkout/restore/stash/reset, không bỏ import/hàm "có vẻ thừa"). Chỉ thêm/sửa đúng chỗ plan nêu.
- Nếu plan sai hoặc thiếu thông tin: dừng lại và nói rõ chỗ nào, đừng tự đoán.
Cuối cùng trả lời ngắn gọn theo mẫu:
FILES: <danh sách file đã sửa>
CHECKS: <lệnh kiểm tra đã chạy và kết quả, hoặc "chưa chạy">
ISSUES: <điểm chưa làm được / cần người xem, hoặc "không">"""


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=PROJECT_DIR, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    return result.stdout.strip()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252, không in được tiếng Việt
    parser =argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", help="đường dẫn file plan (.md)")
    parser.add_argument("--model", default=os.environ.get("AGY_MODEL", "gemini-3.8-flash-high"))
    parser.add_argument("--resume", metavar="CONVERSATION_ID", help="tiếp tục cuộc trò chuyện agy trước")
    parser.add_argument("--note", default="", help="yêu cầu bổ sung (dùng khi --resume để sửa theo review)")
    parser.add_argument("--full", action="store_true", help="cho agy chạy mọi lệnh không hỏi")
    parser.add_argument("--timeout", type=int, default=1800, help="giây, mặc định 1800")
    args = parser.parse_args()

    plan = Path(args.plan)
    if not plan.is_file():
        print(f"Không thấy file plan: {plan}", file=sys.stderr)
        return 2
    plan_rel = plan.resolve().relative_to(PROJECT_DIR).as_posix()
    prompt = INSTRUCTIONS.format(plan=plan_rel)
    if args.note:
        prompt += f"\n\nYêu cầu bổ sung sau review:\n{args.note}"

    command = [os.environ.get("AGY_BIN", str(DEFAULT_AGY)), "-p", prompt,
               "--output-format", "json", "--model", args.model]
    command += ["--dangerously-skip-permissions"] if args.full else ["--mode", "accept-edits"]
    if args.resume:
        command += ["--conversation", args.resume]

    before = set(git("status", "--porcelain").splitlines())
    started = time.time()
    try:
        result = subprocess.run(command, cwd=PROJECT_DIR, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"agy quá {args.timeout}s, đã dừng.", file=sys.stderr)
        return 3

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log = RUNS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{plan.stem}.json"
    log.write_text(result.stdout or result.stderr, encoding="utf-8")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"agy lỗi (mã {result.returncode}). Log: {log}\n{(result.stderr or result.stdout)[-1500:]}")
        return 1

    changed = sorted(line for line in git("status", "--porcelain").splitlines() if line not in before)
    usage = data.get("usage") or {}
    print(f"STATUS: {data.get('status')}  ({time.time() - started:.0f}s, {usage.get('total_tokens', '?')} token agy)")
    print(f"CONVERSATION: {data.get('conversation_id')}")
    print("GIT CHANGES:\n" + ("\n".join(changed) or "(không có file mới thay đổi)"))
    denied = [item.get("display_name") or item.get("action") for item in data.get("denied_actions") or []]
    if denied:
        print(f"DENIED: {', '.join(denied)} (bị chặn vì không có --full; Claude tự chạy kiểm tra)")
    print("REPORT:\n" + (str(data.get("response", "")).strip() or "(agy không gửi báo cáo; xem git diff)"))
    return 0 if data.get("status") == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
