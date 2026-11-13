import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

cmd = [
    gcloud_path,
    "logging", "read",
    'timestamp >= "2026-11-13T11:20:00Z"',
    "--limit=15",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
if p.returncode == 0:
    logs = json.loads(p.stdout)
    for l in logs:
        jp = l.get('jsonPayload') or {}
        msg = jp.get('message') or ""
        if 'Send data' in msg or 'WebSocket' in msg:
            print("TIME:", l.get('timestamp'))
            print("LOG_NAME:", l.get('logName'))
            print("MSG:", msg[:300])
            print("---")

