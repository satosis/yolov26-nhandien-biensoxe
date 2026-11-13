import subprocess
import json

filt = 'timestamp >= "2026-11-13T11:20:00Z"'
cmd = [r'C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd', 'logging', 'read', filt, '--limit=20', '--project=project-33890896-eddc-4353-83a', '--format=json']
p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
logs = json.loads(p.stdout)
for l in logs:
    msg = l.get('jsonPayload', {}).get('message', '')
    if 'execute_request' in msg or 'execute_reply' in msg:
        print('=== ENTRY ===')
        print(msg)

