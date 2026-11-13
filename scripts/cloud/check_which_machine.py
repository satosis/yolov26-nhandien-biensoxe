import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

# Kiểm tra log session trên cả 2 máy trong 5 phút qua
cmd = [
    gcloud_path, "logging", "read",
    'timestamp >= "2026-11-13T12:00:00Z" AND jsonPayload.message =~ "session"',
    "--limit=20",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
if p.returncode == 0:
    logs = json.loads(p.stdout)
    print(f"Số log session tìm thấy: {len(logs)}")
    for l in logs:
        inst = l.get('resource', {}).get('labels', {}).get('instance_id')
        ts = l.get('timestamp')
        msg = l.get('jsonPayload', {}).get('message')
        print(f"[{ts[11:19]}] Inst: {inst} --> {msg[:120]}")
else:
    print("Err:", p.stderr)

