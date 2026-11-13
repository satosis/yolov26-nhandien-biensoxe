import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

cmd = [
    gcloud_path, "logging", "read",
    'timestamp >= "2026-11-13T12:04:00Z"',
    "--limit=25",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
if p.returncode == 0:
    logs = json.loads(p.stdout)
    for l in reversed(logs):
        ts = l.get('timestamp')
        jp = l.get('jsonPayload') or {}
        msg = jp.get('message') or jp.get('msg') or str(jp)
        if '/vertex/euc/health' not in msg:
            print(f"[{ts[11:19]}] {msg[:180]}")

