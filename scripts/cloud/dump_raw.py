import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

cmd = [
    gcloud_path,
    "logging", "read",
    'timestamp >= "2026-11-13T11:25:00Z"',
    "--limit=10",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
if p.returncode == 0:
    logs = json.loads(p.stdout)
    print(f"Tổng log: {len(logs)}")
    for l in logs:
        print(l.get('timestamp'), l.get('logName'), l.get('jsonPayload', {}).get('msg') or l.get('jsonPayload', {}).get('message') or l.get('textPayload'))

