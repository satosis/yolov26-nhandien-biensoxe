import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

cmd = [
    gcloud_path,
    "logging", "read",
    'timestamp >= "2026-11-13T10:30:00Z"',
    "--limit=100",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
if p.returncode == 0:
    logs = json.loads(p.stdout)
    print(f"Tổng số bản ghi: {len(logs)}")
    streams = []
    for l in logs:
        jp = l.get('jsonPayload') or {}
        tp = l.get('textPayload') or ""
        msg = jp.get('message') or jp.get('msg') or tp or str(jp)
        # Bỏ qua health checks
        if '/vertex/euc/health' in msg:
            continue
        streams.append(f"[{l.get('timestamp')[:19]}] {msg[:150]}")
    for s in streams[:25]:
        print(s)
else:
    print("Err:", p.stderr)

