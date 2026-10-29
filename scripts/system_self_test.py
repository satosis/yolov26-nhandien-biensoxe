#!/usr/bin/env python3
"""Automated System-Wide Self-Test Suite for Parking Camera AI & ERP Portal.

Performs multi-phase health and regression checks:
1. Database connectivity & critical table schema integrity.
2. Core accounting calculations & business service sanity checks.
3. Automated route discovery & HTTP response status scan across all registered endpoints.
"""

from __future__ import annotations

import base64
from datetime import date, datetime
import json
import logging
import os
import sys
import time
from typing import Any, Callable, Dict, List, Tuple

# Ensure project root in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("self_test")


def check_database_integrity() -> Dict[str, Any]:
    """Test DB connection and verify critical tables."""
    from backend.services.database import db
    critical_tables = [
        "public.invoice_companies",
        "public.users",
        "public.roles",
        "public.cargo_trips",
        "public.transport_cash_book",
        "public.transport_contracts",
        "public.transport_input_invoices",
        "public.transport_prepaid_expenses",
        "public.advertising_campaigns",
        "public.cargo_catalog_items",
        "public.cameras",
        "public.sites",
    ]
    passed = []
    failed = []

    for tbl in critical_tables:
        try:
            row = db._execute_query(f"SELECT COUNT(*) as cnt FROM {tbl}", fetch=True, commit=False)
            cnt = row.get("cnt", 0) if row else 0
            passed.append({"table": tbl, "count": cnt})
        except Exception as e:
            failed.append({"table": tbl, "error": str(e)})

    return {
        "status": "PASS" if not failed else "FAIL",
        "passed": passed,
        "failed": failed,
        "total_checked": len(critical_tables),
    }


def check_business_services(company_id: int = 33) -> Dict[str, Any]:
    """Test core business services and accounting calculations."""
    results = []
    from frontend.context.company_context import company_context

    with company_context(company_id):
        # 1. Cash Book Data calculation
        try:
            from frontend.modules.transport.cash_bank_service import get_cash_book_data
            cb_res = get_cash_book_data(company_id=company_id, year=2026)
            results.append({"name": "Dịch vụ Sổ quỹ tiền mặt (S04a-DNN)", "status": "PASS", "details": f"{len(cb_res.get('entries', []))} chứng từ"})
        except Exception as e:
            results.append({"name": "Dịch vụ Sổ quỹ tiền mặt (S04a-DNN)", "status": "FAIL", "error": str(e)})

        # 2. Bank Book Data calculation
        try:
            from frontend.modules.transport.cash_bank_service import get_bank_book_data
            bb_res = get_bank_book_data(company_id=company_id, year=2026)
            results.append({"name": "Dịch vụ Sổ tiền gửi NH (S05a-DNN)", "status": "PASS", "details": f"{len(bb_res.get('entries', []))} chứng từ"})
        except Exception as e:
            results.append({"name": "Dịch vụ Sổ tiền gửi NH (S05a-DNN)", "status": "FAIL", "error": str(e)})

        # 3. Trial Balance calculation
        try:
            from frontend.modules.transport.trial_balance_service import get_trial_balance_data
            tb_res = get_trial_balance_data(company_id=company_id, year=2026)
            results.append({"name": "Bảng cân đối tài khoản (F01-DNN)", "status": "PASS", "details": f"{len(tb_res.get('all_rows', []))} tài khoản"})
        except Exception as e:
            results.append({"name": "Bảng cân đối tài khoản (F01-DNN)", "status": "FAIL", "error": str(e)})

        # 4. Depreciation calculation
        try:
            from frontend.modules.transport.depreciation_service import get_depreciation_data
            dp_res = get_depreciation_data(company_id=company_id, year=2026)
            results.append({"name": "Bảng tính khấu hao TSCĐ", "status": "PASS", "details": f"{len(dp_res.get('rows', []))} tài sản"})
        except Exception as e:
            results.append({"name": "Bảng tính khấu hao TSCĐ", "status": "FAIL", "error": str(e)})

        # 5. Prepaid expenses (CCDC TK 242)
        try:
            from frontend.modules.transport.prepaid_expense_service import get_prepaid_expenses_data
            pe_res = get_prepaid_expenses_data(company_id=company_id, year=2026)
            results.append({"name": "Phân bổ CCDC (TK 242)", "status": "PASS", "details": f"{len(pe_res.get('items', []))} công cụ"})
        except Exception as e:
            results.append({"name": "Phân bổ CCDC (TK 242)", "status": "FAIL", "error": str(e)})

        # 6. Advertising Module
        try:
            from frontend.modules.advertising.service import get_advertising_kpi_summary, list_campaigns
            kpi = get_advertising_kpi_summary()
            camps = list_campaigns()
            results.append({"name": "Module Quảng Cáo Google Ads", "status": "PASS", "details": f"{len(camps)} chiến dịch, KPI active: {kpi.get('active_count', 0)}"})
        except Exception as e:
            results.append({"name": "Module Quảng Cáo Google Ads", "status": "FAIL", "error": str(e)})

    fails = [r for r in results if r["status"] == "FAIL"]
    return {
        "status": "PASS" if not fails else "FAIL",
        "services": results,
    }


def scan_all_get_routes(progress_cb: Callable[[int, int, str, int], None] | None = None) -> Dict[str, Any]:
    """Test all GET routes across the entire portal using Starlette TestClient with an authenticated session."""
    from itsdangerous import TimestampSigner
    from starlette.testclient import TestClient
    from frontend.app import app

    sec = os.environ.get("SESSION_SECRET", "default-secret-key-123")
    signer = TimestampSigner(sec)
    session_data = json.dumps({
        "user": {"id": 1, "username": "admin", "role": "admin", "allowed_menus": "*"},
        "active_company_id": 33,
    }).encode("utf-8")
    cookie_val = signer.sign(base64.b64encode(session_data)).decode("utf-8")
    client = TestClient(app, cookies={"parking_session": cookie_val}, raise_server_exceptions=False)

    routes_to_test = []
    for route in app.routes:
        if hasattr(route, "methods") and "GET" in route.methods:
            p = route.path
            # Skip parameterized routes for generic smoke scan
            if "{" in p:
                continue
            routes_to_test.append(p)

    routes_to_test = sorted(list(set(routes_to_test)))
    total = len(routes_to_test)
    passed = []
    failed = []

    for idx, path in enumerate(routes_to_test):
        try:
            r = client.get(path, follow_redirects=False)
            status_code = r.status_code
            if status_code < 500:
                passed.append({"path": path, "status": status_code})
            else:
                failed.append({"path": path, "status": status_code, "error": r.text[:200]})
        except Exception as e:
            failed.append({"path": path, "status": 500, "error": str(e)})

        if progress_cb:
            progress_cb(idx + 1, total, path, status_code)

    return {
        "status": "PASS" if not failed else "FAIL",
        "total": total,
        "passed_count": len(passed),
        "failed_count": len(failed),
        "failed_routes": failed,
    }


def run_full_self_test(on_progress: Callable[[str, Dict[str, Any]], None] | None = None) -> Dict[str, Any]:
    """Execute complete self-test suite and return consolidated report."""
    start_time = time.time()
    report: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "database": None,
        "services": None,
        "routes": None,
        "summary": {},
    }

    # 1. DB
    db_res = check_database_integrity()
    report["database"] = db_res
    if on_progress:
        on_progress("database", db_res)

    # 2. Services
    srv_res = check_business_services()
    report["services"] = srv_res
    if on_progress:
        on_progress("services", srv_res)

    # 3. Routes
    def _route_cb(cur, tot, p, code):
        pass

    route_res = scan_all_get_routes(progress_cb=_route_cb)
    report["routes"] = route_res
    if on_progress:
        on_progress("routes", route_res)

    duration = round(time.time() - start_time, 2)
    overall_ok = (
        db_res["status"] == "PASS" and
        srv_res["status"] == "PASS" and
        route_res["status"] == "PASS"
    )

    report["summary"] = {
        "overall_status": "PASS" if overall_ok else "FAIL",
        "duration_seconds": duration,
        "total_routes_tested": route_res["total"],
        "route_errors": route_res["failed_count"],
        "database_ok": db_res["status"] == "PASS",
        "services_ok": srv_res["status"] == "PASS",
    }

    return report


if __name__ == "__main__":
    print("=" * 70)
    print("🚀 BẮT ĐẦU CHẠY SELF-TEST TOÀN BỘ HỆ THỐNG")
    print("=" * 70)

    report = run_full_self_test()

    print(f"\n1. Cơ sở dữ liệu: [{report['database']['status']}]")
    for tbl in report["database"]["passed"]:
        print(f"   ✓ {tbl['table']}: {tbl['count']} bản ghi")
    for fl in report["database"]["failed"]:
        print(f"   ✗ {fl['table']}: Lỗi {fl['error']}")

    print(f"\n2. Dịch vụ & Nghiệp vụ kế toán: [{report['services']['status']}]")
    for s in report["services"]["services"]:
        mark = "✓" if s["status"] == "PASS" else "✗"
        det = s.get("details") or s.get("error")
        print(f"   {mark} {s['name']}: {det}")

    print(f"\n3. Quét toàn bộ Routes ({report['routes']['total']} endpoints): [{report['routes']['status']}]")
    print(f"   ✓ Thành công: {report['routes']['passed_count']} routes")
    if report["routes"]["failed_count"] > 0:
        print(f"   ✗ Thất bại: {report['routes']['failed_count']} routes")
        for f in report["routes"]["failed_routes"]:
            print(f"     - {f['path']} ({f['status']}): {f['error']}")

    print("=" * 70)
    summary = report["summary"]
    status_text = "🎉 TOÀN BỘ HỆ THỐNG HOẠT ĐỘNG HOÀN HẢO (100% PASS)" if summary["overall_status"] == "PASS" else "⚠️ CÓ LỖI CẦN XỬ LÝ"
    print(f"KẾT QUẢ: {status_text}")
    print(f"Thời gian kiểm tra: {summary['duration_seconds']}s")
    print("=" * 70)
    sys.exit(0 if summary["overall_status"] == "PASS" else 1)
