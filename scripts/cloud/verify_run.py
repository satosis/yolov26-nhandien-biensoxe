import subprocess
import json
import time

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

print("==================================================================")
print("🔍 KIỂM TRA TRẠNG THÁI RUNTIME MONSTER-32VCPU-128GB-L4")
print("==================================================================")
cmd_rt = [gcloud_path, "colab", "runtimes", "describe", "5900388412971548672", "--region=asia-southeast1", "--format=json"]
p = subprocess.run(cmd_rt, capture_output=True, text=True, encoding="utf-8")
if p.returncode == 0:
    rt = json.loads(p.stdout)
    print(f"Tên máy:     {rt.get('displayName')}")
    print(f"Trạng thái:  {rt.get('runtimeState')}")
    print(f"Sức khỏe:    {rt.get('healthState')}")
    print(f"Cấu hình:    {rt.get('machineSpec')}")
else:
    print("Lỗi đọc runtime:", p.stderr)

print("\n==================================================================")
print("🔍 KIỂM TRA NHẬT KÝ THỰC THI (JUPYTER KERNEL & CONTAINER LOGS)")
print("==================================================================")
cmd_log = [
    gcloud_path, "logging", "read",
    'timestamp >= "2026-11-13T11:58:00Z"',
    "--limit=50",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
]
p_log = subprocess.run(cmd_log, capture_output=True, text=True, encoding="utf-8")
if p_log.returncode == 0 and p_log.stdout.strip():
    logs = json.loads(p_log.stdout)
    print(f"Tổng số bản ghi mới nhận: {len(logs)}")
    for l in reversed(logs):
        ts = l.get('timestamp', '')
        jp = l.get('jsonPayload') or {}
        tp = l.get('textPayload') or ""
        msg = jp.get('message') or jp.get('msg') or tp or str(jp)
        if '/vertex/euc/health' not in msg:
            print(f"[{ts[11:19]}] {msg[:160]}")
else:
    print("Chưa có log mới:", p_log.stderr)

