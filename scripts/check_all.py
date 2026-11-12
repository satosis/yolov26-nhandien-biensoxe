#!/usr/bin/env python3
"""
scripts/check_all.py — Chạy toàn bộ kiểm tra cho dự án:
1. node --test trong scripts/zalo_bridge
2. Biên dịch toàn bộ Jinja2 templates trong frontend/templates
3. node --check cho các file JavaScript tự viết
In bảng TÊN | OK/LỖI | thời gian, exit 1 nếu có lỗi.
"""

import os
import sys
import time
import subprocess
from pathlib import Path

# Console Windows cp1252 fix
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent
os.chdir(ROOT_DIR)
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

ROW_FORMAT = "{:<55} | {:^8} | {:>9}"


def print_row(name: str, status: str, duration_sec: float) -> None:
    print(ROW_FORMAT.format(name, status, f"{duration_sec:.2f}s"), flush=True)


def main() -> int:
    start_total = time.time()
    results: list[tuple[str, str, float, str]] = []  # (name, status, elapsed, error_detail)

    print("=" * 77, flush=True)
    print(ROW_FORMAT.format("TÊN", "OK/LỖI", "THỜI GIAN"), flush=True)
    print("=" * 77, flush=True)

    # 1. node --test trong scripts/zalo_bridge
    t0 = time.time()
    zb_dir = ROOT_DIR / "scripts" / "zalo_bridge"
    try:
        proc = subprocess.run(
            ["node", "--test"],
            cwd=str(zb_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        elapsed = time.time() - t0
        item_name = "node --test (scripts/zalo_bridge)"
        if proc.returncode == 0:
            print_row(item_name, "OK", elapsed)
            results.append((item_name, "OK", elapsed, ""))
        else:
            err_detail = proc.stderr.strip() or proc.stdout.strip()
            print_row(item_name, "LỖI", elapsed)
            results.append((item_name, "LỖI", elapsed, err_detail))
    except Exception as exc:
        elapsed = time.time() - t0
        item_name = "node --test (scripts/zalo_bridge)"
        print_row(item_name, "LỖI", elapsed)
        results.append((item_name, "LỖI", elapsed, str(exc)))

    # 2. Biên dịch mọi template Jinja2
    t0 = time.time()
    item_name = "Biên dịch Jinja2 templates (frontend/templates)"
    tpl_dir = ROOT_DIR / "frontend" / "templates"
    tpl_files = sorted(tpl_dir.rglob("*.html"))
    tpl_errors: list[str] = []
    try:
        from jinja2.exceptions import TemplateSyntaxError
        from frontend.deps import templates

        for tpl in tpl_files:
            rel = tpl.relative_to(tpl_dir).as_posix()
            try:
                templates.env.get_template(rel)
            except TemplateSyntaxError as syn_err:
                tpl_errors.append(f"{rel}:{syn_err.lineno}: {syn_err.message}")
            except Exception as tpl_exc:
                tpl_errors.append(f"{rel}: {tpl_exc}")
        elapsed = time.time() - t0
        if not tpl_errors:
            print_row(f"{item_name} [{len(tpl_files)} files]", "OK", elapsed)
            results.append((item_name, "OK", elapsed, ""))
        else:
            print_row(f"{item_name} [{len(tpl_files)} files]", "LỖI", elapsed)
            results.append((item_name, "LỖI", elapsed, "\n".join(tpl_errors)))
    except Exception as exc:
        elapsed = time.time() - t0
        print_row(item_name, "LỖI", elapsed)
        results.append((item_name, "LỖI", elapsed, str(exc)))

    # 3. node --check cho mọi .js tự viết
    static_dir = ROOT_DIR / "frontend" / "static"
    js_static = [
        p for p in static_dir.rglob("*.js")
        if "vendor" not in p.parts and not p.name.endswith(".min.js")
    ]
    js_zalo = [p for p in (ROOT_DIR / "scripts" / "zalo_bridge").glob("*.js")]
    all_js = sorted(js_static + js_zalo)

    for js_path in all_js:
        rel_js = js_path.relative_to(ROOT_DIR).as_posix()
        item_label = f"node --check {rel_js}"
        t0 = time.time()
        try:
            proc = subprocess.run(
                ["node", "--check", str(js_path)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            elapsed = time.time() - t0
            if proc.returncode == 0:
                print_row(item_label, "OK", elapsed)
                results.append((item_label, "OK", elapsed, ""))
            else:
                err_detail = proc.stderr.strip() or proc.stdout.strip()
                print_row(item_label, "LỖI", elapsed)
                results.append((item_label, "LỖI", elapsed, err_detail))
        except Exception as exc:
            elapsed = time.time() - t0
            print_row(item_label, "LỖI", elapsed)
            results.append((item_label, "LỖI", elapsed, str(exc)))

    # In tổng kết
    total_time = time.time() - start_total
    ok_count = sum(1 for _, status, _, _ in results if status == "OK")
    err_count = sum(1 for _, status, _, _ in results if status != "OK")

    print("=" * 77, flush=True)
    print(f"TỔNG KẾT: {ok_count} OK, {err_count} LỖI | Tổng thời gian: {total_time:.2f}s", flush=True)
    print("=" * 77, flush=True)

    if err_count > 0:
        print("\nDANH SÁCH CHI TIẾT CÁC MỤC LỖI:\n", flush=True)
        for name, status, _, detail in results:
            if status != "OK":
                print(f"[{status}] {name}:", flush=True)
                lines = detail.strip().splitlines()
                # Print last 30 lines of error detail (where actual traceback/error summary is)
                display_lines = lines[-30:] if len(lines) > 30 else lines
                if len(lines) > 30:
                    print(f"    ... (bỏ qua {len(lines) - 30} dòng log đầu)", flush=True)
                for line in display_lines:
                    print(f"    {line}", flush=True)
                print(flush=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
