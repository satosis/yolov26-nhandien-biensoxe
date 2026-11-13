import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

# Đọc log chi tiết của cos_containers và payload-container
cmd = [
    gcloud_path,
    "logging", "read",
    'timestamp >= "2026-11-13T11:00:00Z"',
    "--limit=60",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
if p.returncode == 0:
    logs = json.loads(p.stdout)
    print(f"Tổng số bản ghi từ 18:00 VN: {len(logs)}")
    for l in reversed(logs):
        ts = l.get('timestamp')
        jp = l.get('jsonPayload') or {}
        tp = l.get('textPayload') or ""
        msg = jp.get('message') or jp.get('msg') or tp or str(jp)
        # Chỉ in các dòng có ý nghĩa tiến trình
        if any(k in msg.lower() for k in ['execute', 'epoch', 'yolo', 'ultralytics', 'download', 'kaggle', 'batch', 'cuda', 'gpu', 'busy', 'idle', 'stream', 'stdout', 'error']):
            print(f"[{ts[:19]}] {msg[:200]}")
else:
    print("Error:", p.stderr)

