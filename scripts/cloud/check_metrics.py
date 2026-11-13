import subprocess
import json

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

# Kiểm tra metric CPU utilization của instance
cmd = [
    gcloud_path,
    "monitoring", "timeseries", "list",
    'metric.type="compute.googleapis.com/instance/cpu/utilization"',
    '--filter=resource.labels.instance_id="3032894522055687936"',
    '--limit=5',
    '--project=project-33890896-eddc-4353-83a',
    '--format=json'
]
p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
if p.returncode == 0 and p.stdout.strip():
    ts = json.loads(p.stdout)
    print("=== METRICS CPU ===")
    for s in ts:
        points = s.get('points', [])
        for pt in points[:5]:
            val = pt.get('value', {}).get('doubleValue', 0.0)
            end_t = pt.get('interval', {}).get('endTime')
            print(f"Time: {end_t} | CPU Usage: {val * 100:.2f}%")
else:
    print("Metrics output / err:", p.stdout, p.stderr)

