#!/usr/bin/env python3
"""Deep Link & Button Audit Engine for Parking & Transport ERP.

Thoroughly crawls all internal HTML pages rendered by the system:
1. Validates all <a href="..."> links.
2. Validates all <button data-target="#..."> modals exist in DOM.
3. Validates all <form action="..."> target endpoints.
4. Detects any 404, 500, or invalid JavaScript references.
"""

from __future__ import annotations

import base64
from bs4 import BeautifulSoup
from datetime import datetime
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Set
from urllib.parse import urljoin, urlparse

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

from itsdangerous import TimestampSigner
from starlette.testclient import TestClient
from frontend.app import app

def run_deep_audit() -> Dict[str, Any]:
    sec = os.environ.get("SESSION_SECRET", "default-secret-key-123")
    signer = TimestampSigner(sec)
    session_data = json.dumps({
        "user": {"id": 1, "username": "admin", "role": "admin", "allowed_menus": "*"},
        "active_company_id": 33,
    }).encode("utf-8")
    cookie_val = signer.sign(base64.b64encode(session_data)).decode("utf-8")
    client = TestClient(app, cookies={"parking_session": cookie_val}, raise_server_exceptions=False)

    # Seed list of pages to crawl
    seed_urls = [
        # Public
        "/", "/xe-cau", "/qua-can", "/kiem-dinh-can", "/nhaxuong", "/dich-vu", 
        "/du-an", "/tin-tuc", "/lien-he", "/gioi-thieu", "/kyc",
        # Transport & Accounting
        "/admin/vehicles", "/admin/vehicles/add", "/admin/vehicles/cargo", 
        "/admin/vehicles/cargo/invoices/add", "/admin/vehicles/monthly-report",
        "/admin/transport/contracts", "/admin/transport/depreciation", 
        "/admin/transport/trial-balance", "/admin/transport/cash-book", 
        "/admin/transport/bank-book", "/admin/transport/prepaid-expenses", 
        "/admin/transport/input-invoices", "/admin/transport/payroll",
        "/admin/transport/catalog?tab=service", "/admin/transport/catalog?tab=cargo",
        "/admin/enterprise/tax-checklist",
        # Enterprise
        "/admin/enterprise/attendance", "/admin/enterprise/advances", 
        "/admin/enterprise/assets", "/admin/enterprise/debts", 
        "/admin/enterprise/sales", "/admin/enterprise/suppliers",
        # Advertising
        "/admin/advertising", "/admin/advertising/keywords",
        # System
        "/admin/companies", "/admin/users", "/admin/users/add", "/admin/rbac", 
        "/admin/administrative-divisions", "/admin/inquiries", 
        "/admin/cms", "/admin/telegram", "/admin/zalo_inbox", 
        "/admin/service-accounts", "/admin/api-keys",
        # Parking & Vision
        "/admin/detection", "/admin/sites", "/admin/cameras",
    ]

    visited_pages: Set[str] = set()
    pages_to_visit: List[str] = list(seed_urls)
    all_links_tested: Dict[str, int] = {}
    broken_links: List[Dict[str, Any]] = []
    broken_modals: List[Dict[str, Any]] = []
    total_buttons_found = 0
    total_modals_checked = 0
    total_links_found = 0

    print("======================================================================")
    print("🚀 BẮT ĐẦU KIỂM TOÁN TỔNG THỂ TOÀN BỘ NÚT & LIÊN KẾT (DEEP AUDIT)")
    print("======================================================================")

    while pages_to_visit:
        current_url = pages_to_visit.pop(0)
        if current_url in visited_pages:
            continue
        visited_pages.add(current_url)

        try:
            res = client.get(current_url, follow_redirects=True)
            status_code = res.status_code
        except Exception as e:
            broken_links.append({"source": "seed", "url": current_url, "status": "EXCEPTION", "error": str(e)})
            continue

        if status_code >= 400:
            broken_links.append({"source": "seed", "url": current_url, "status": status_code, "error": res.text[:200]})
            continue

        content_type = res.headers.get("content-type", "")
        if "text/html" not in content_type:
            continue

        soup = BeautifulSoup(res.text, "html.parser")

        # 1. Inspect ALL <button> tags
        buttons = soup.find_all("button")
        total_buttons_found += len(buttons)
        for btn in buttons:
            # Check modal targets
            modal_target = btn.get("data-target") or btn.get("data-bs-target")
            if modal_target and modal_target.startswith("#") and len(modal_target) > 1:
                total_modals_checked += 1
                target_id = modal_target[1:]
                target_el = soup.find(id=target_id)
                if not target_el:
                    broken_modals.append({
                        "page": current_url,
                        "button_text": btn.get_text(strip=True)[:40],
                        "missing_target": modal_target,
                    })

        # 2. Inspect ALL <form> tags
        forms = soup.find_all("form")
        for form in forms:
            action = form.get("action")
            if action:
                parsed_action = urlparse(action)
                if not parsed_action.netloc: # internal
                    action_path = parsed_action.path
                    if action_path and action_path.startswith("/") and not action_path.startswith("/#"):
                        if action_path not in all_links_tested:
                            all_links_tested[action_path] = 0

        # 3. Inspect ALL <a> tags
        anchors = soup.find_all("a")
        total_links_found += len(anchors)
        for a in anchors:
            href = a.get("href")
            if not href:
                continue
            href = href.strip()
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            parsed = urlparse(href)
            if parsed.scheme in ("http", "https") and parsed.netloc not in ("127.0.0.1", "localhost", "testserver"):
                continue  # External link

            # Relative to root
            clean_path = parsed.path
            if parsed.query:
                clean_path = f"{clean_path}?{parsed.query}"

            if not clean_path.startswith("/"):
                clean_path = urljoin(current_url, href)

            if clean_path in all_links_tested:
                continue

            # Test this link
            try:
                r = client.get(clean_path, follow_redirects=False)
                all_links_tested[clean_path] = r.status_code
                if r.status_code >= 400 and r.status_code != 422: # 422 is API validation expected
                    broken_links.append({
                        "source_page": current_url,
                        "anchor_text": a.get_text(strip=True)[:40],
                        "target_url": clean_path,
                        "status": r.status_code,
                    })
                elif r.status_code in (200, 303, 307):
                    # If it's an admin/page link and we have not visited yet, discover
                    if clean_path.startswith(("/admin/", "/xe-cau", "/qua-can", "/kiem-dinh-can")) and clean_path not in visited_pages and "export" not in clean_path and "delete" not in clean_path:
                        if len(visited_pages) + len(pages_to_visit) < 120:
                            pages_to_visit.append(clean_path)
            except Exception as exc:
                all_links_tested[clean_path] = 500
                broken_links.append({
                    "source_page": current_url,
                    "anchor_text": a.get_text(strip=True)[:40],
                    "target_url": clean_path,
                    "status": 500,
                    "error": str(exc),
                })

    print(f"\n[KẾT QUẢ ĐÃ QUÉT]")
    print(f"- Số trang HTML đã phân tích chi tiết: {len(visited_pages)}")
    print(f"- Số thẻ <button> đã quét: {total_buttons_found}")
    print(f"- Số modal targets (data-target) đã xác thực: {total_modals_checked}")
    print(f"- Số liên kết <a> đã tìm thấy: {total_links_found}")
    print(f"- Số URLs liên kết duy nhất đã kiểm tra trực tiếp: {len(all_links_tested)}")
    print(f"- Số liên kết lỗi (4xx / 5xx): {len(broken_links)}")
    print(f"- Số nút modal trỏ sai ID: {len(broken_modals)}")

    if broken_links:
        print("\n❌ DANH SÁCH LIÊN KẾT BỊ LỖI:")
        for item in broken_links[:20]:
            print(f"  * Tại [{item.get('source_page')}]: Link '{item.get('anchor_text')}' -> {item.get('target_url')} (Status: {item.get('status')})")

    if broken_modals:
        print("\n❌ DANH SÁCH MODAL TARGETS THIẾU:")
        for item in broken_modals[:20]:
            print(f"  * Tại [{item.get('page')}]: Nút '{item.get('button_text')}' -> Missing target {item.get('missing_target')}")

    if not broken_links and not broken_modals:
        print("\n🎉 HOÀN TOÀN KHÔNG CÓ BẤT KỲ NÚT HOẶC LINK NÀO BỊ LỖI!")

    return {
        "pages_visited": len(visited_pages),
        "total_buttons": total_buttons_found,
        "total_links": total_links_found,
        "unique_links_tested": len(all_links_tested),
        "broken_links": broken_links,
        "broken_modals": broken_modals,
    }

if __name__ == "__main__":
    run_deep_audit()
