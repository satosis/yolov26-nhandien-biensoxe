import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

cmd = [
    gcloud_path, "logging", "read",
    'resource.labels.instance_id="3032894522055687936" AND timestamp >= "2026-11-13T12:20:00Z"',
    "--limit=200",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
if p.returncode == 0:
    logs = json.loads(p.stdout)
    errors = []
    for l in logs:
        msg = l.get('jsonPayload', {}).get('message') or l.get('textPayload') or ""
        if any(w in msg.lower() for w in ['traceback', 'exception', 'failed', '401', '403', 'unauthorized', 'not found', 'error']):
            if 'pip' not in msg and 'route' not in msg:
                errors.append((l.get('timestamp'), msg))
    print(f"Số lỗi nghiêm trọng tìm thấy: {len(errors)}")
    for ts, m in errors[:10]:
        print(ts, "-->", m[:150])

