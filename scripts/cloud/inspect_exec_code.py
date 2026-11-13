import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

cmd = [
    gcloud_path,
    "logging", "read",
    'timestamp >= "2026-11-13T11:20:00Z" AND jsonPayload.message =~ "execute"',
    "--limit=15",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
if p.returncode == 0:
    logs = json.loads(p.stdout)
    print(f"Số bản ghi execute: {len(logs)}")
    for l in logs:
        msg = l.get('jsonPayload', {}).get('message', '')
        print("---")
        print(f"[{l.get('timestamp')}]")
        print(msg)
else:
    print("Err:", p.stderr)

