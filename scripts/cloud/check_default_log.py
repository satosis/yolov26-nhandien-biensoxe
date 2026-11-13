import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

cmd = [
    gcloud_path, "logging", "read",
    'resource.labels.instance_id="1624716479618079540" AND timestamp >= "2026-11-13T12:00:00Z"',
    "--limit=30",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
if p.returncode == 0:
    logs = json.loads(p.stdout)
    print(f"Log count from Default machine: {len(logs)}")
    for l in logs:
        msg = l.get('jsonPayload', {}).get('message') or l.get('textPayload') or ""
        print(f"[{l.get('timestamp')[11:19]}] {msg[:150]}")
else:
    print("Err:", p.stderr)

