import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

cmd = [
    gcloud_path, "logging", "read",
    'resource.labels.instance_id="3032894522055687936" AND timestamp >= "2026-11-13T12:10:00Z"',
    "--limit=100",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
if p.returncode == 0:
    logs = json.loads(p.stdout)
    exec_found = False
    for l in logs:
        msg = l.get('jsonPayload', {}).get('message') or ""
        if any(k in msg.lower() for k in ['execute', 'busy', 'stream', 'pip', 'kaggle', 'epoch', 'torch']):
            print(f"[{l.get('timestamp')[11:19]}] EXEC LOG: {msg[:160]}")
            exec_found = True
    if not exec_found:
        print("Trạng thái hiện tại: Kernel đang IDLE (SẴN SÀNG), chưa có ô code nào đang chạy.")

