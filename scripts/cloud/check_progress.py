import subprocess
import json
import sys

gcloud_path = r"C:\Users\admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

def run_gcloud(args):
    cmd = [gcloud_path] + args
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    return p.stdout, p.stderr, p.returncode

print("=== 1. KIỂM TRA RUNTIME VERTEX AI ===")
out, err, code = run_gcloud(["colab", "runtimes", "list", "--region=asia-southeast1", "--project=project-33890896-eddc-4353-83a", "--format=json"])
if code == 0:
    runtimes = json.loads(out)
    for r in runtimes:
        print(f"Name: {r.get('displayName')} | State: {r.get('runtimeState')} | Health: {r.get('healthState')}")
        print(f"ID: {r.get('name')}")
else:
    print("Lỗi check runtime:", err)

print("\n=== 2. KIỂM TRA BUCKET CLOUD STORAGE ===")
out, err, code = run_gcloud(["storage", "ls", "--recursive", "gs://yolo26-plate-train-686766750544/**", "--format=json"])
if code == 0:
    try:
        items = json.loads(out)
        for it in items:
            print(f"- {it.get('name')} ({it.get('size')} bytes, updated {it.get('updated')})")
    except Exception:
        print(out)
else:
    print("Storage ls output / error:", out, err)

print("\n=== 3. KIỂM TRA NHẬT KÝ CONTAINER VÀ TIẾN TRÌNH GẦN NHẤT ===")
out, err, code = run_gcloud([
    "logging", "read",
    'resource.type="aiplatform.googleapis.com/NotebookRuntime" OR resource.type="gce_instance"',
    "--limit=25",
    "--project=project-33890896-eddc-4353-83a",
    "--format=json"
])
if code == 0 and out.strip():
    try:
        logs = json.loads(out)
        print(f"Số bản ghi nhật ký tìm thấy: {len(logs)}")
        for l in logs:
            t = l.get('timestamp')
            p = l.get('textPayload') or l.get('jsonPayload') or {}
            msg = p.get('message') or p.get('msg') or str(p)
            print(f"[{t}] {msg[:160]}")
    except Exception as e:
        print("Parse error:", e, out[:300])
else:
    print("Chưa có log mới hoặc lỗi:", err)

