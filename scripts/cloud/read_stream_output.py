import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

cmd = [
    gcloud_path, "logging", "read",
    'timestamp >= "2026-11-13T12:03:50Z"',
    "--limit=25",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
if p.returncode == 0:
    logs = json.loads(p.stdout)
    print("=== LIVE STREAM OUTPUT FROM GOOGLE CLOUD ===")
    for l in reversed(logs):
        ts = l.get('timestamp')
        msg = l.get('jsonPayload', {}).get('message', '')
        if 'stream' in msg or 'execute' in msg or 'colab' in msg:
            print(f"[{ts[11:19]}]")
            print(msg[:300])
            print("-" * 50)

