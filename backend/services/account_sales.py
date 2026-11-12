"""Quản lý sản phẩm và đơn hàng các dịch vụ (tài khoản số, container, quả cân, điện mặt trời, máy ảnh & photobooth)."""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from backend.services.database import db

# Tài khoản nhận tiền hiển thị bằng mã QR VietQR trên trang mua hàng.
PAY_BANK_CODE = "MB"
PAY_BANK_NAME = "MB Bank"
PAY_ACCOUNT = "0948561668"
PAY_ACCOUNT_NAME = "NGUYEN MANH HUNG"

ORDER_STATUSES: dict[str, str] = {
    "new": "Mới",
    "paid": "Đã thanh toán",
    "delivered": "Đã giao",
    "cancelled": "Đã hủy",
}
ADMIN_ORDER_STATUSES: dict[str, str] = {
    "paid": "Đã thanh toán",
    "delivered": "Đã giao",
}
PAID_STATUSES: tuple[str, ...] = ("paid", "delivered")

_PAYMENT_CODE_RE = re.compile(r"TK[0-9A-F]{6}")
_DRAFT_TTL_SEC = 48 * 3600
_DRAFT_MAX_ITEMS = 5000
_PENDING_DRAFTS: dict[str, dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()


# Bảng account_products và account_orders dùng chung cho mọi trang bán; cột `service` cho biết thuộc dịch vụ nào.
ACCOUNT_SERVICE = "tai-khoan"
SERVICES: dict[str, str] = {
    ACCOUNT_SERVICE: "Bán tài khoản",
    "thue-container": "Container",
    "qua-can": "Quả cân",
    "dien-mat-troi": "Điện mặt trời",
    "thue-may-anh": "Máy ảnh và photobooth",
}
SERVICE_PATHS: dict[str, str] = {
    ACCOUNT_SERVICE: "/tai-khoan",
    "thue-container": "/thue-container",
    "qua-can": "/qua-can",
    "dien-mat-troi": "/dien-mat-troi",
    "thue-may-anh": "/thue-may-anh",
}


def ensure_catalog_columns() -> bool:
    """Thêm các cột của danh mục chung nếu bảng chưa có; trả True khi vừa thêm (lần chạy đầu tiên)."""
    exists = db.query("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'account_products' AND column_name = 'service'
    """, fetch=True)
    if exists:
        return False
    db.query(f"""
        ALTER TABLE public.account_products
            ADD COLUMN IF NOT EXISTS service text NOT NULL DEFAULT '{ACCOUNT_SERVICE}',
            ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS specs text NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS icon text NOT NULL DEFAULT '';
        CREATE INDEX IF NOT EXISTS ix_account_products_service ON public.account_products (service);
    """)
    return True


def ensure_order_columns() -> bool:
    """Thêm cột dịch vụ cho bảng đơn hàng nếu chưa có và đồng bộ dữ liệu cũ theo sản phẩm."""
    exists = db.query("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'account_orders' AND column_name = 'service'
    """, fetch=True)
    if exists:
        return False
    db.query(f"""
        ALTER TABLE public.account_orders
            ADD COLUMN IF NOT EXISTS service text NOT NULL DEFAULT '{ACCOUNT_SERVICE}';
        CREATE INDEX IF NOT EXISTS ix_account_orders_service ON public.account_orders (service);
        UPDATE public.account_orders o
        SET service = COALESCE(NULLIF(p.service, ''), '{ACCOUNT_SERVICE}')
        FROM public.account_products p
        WHERE o.product_id = p.id;
    """)
    return True


def image_url(image: str) -> str:
    """Ảnh tải lên nằm ở /upload/accounts; ảnh có sẵn của trang dịch vụ lưu nguyên đường dẫn /static/..."""
    if not image:
        return ""
    return image if image.startswith("/") else f"/upload/accounts/{image}"


def _products_where(q: str, available_only: bool, service: str = "") -> tuple[str, list]:
    where, params = [], []
    if available_only:
        where.append("active AND stock > 0")
    if service:
        where.append("service = %s")
        params.append(service)
    if q:
        where.append("(name ILIKE %s OR category ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    return (" WHERE " + " AND ".join(where)) if where else "", params


def list_products(q: str = "", available_only: bool = False, group: str = "", category: str = "",
                  limit: int | None = None, offset: int = 0, service: str = "") -> list[dict[str, Any]]:
    """Với `available_only` chỉ lấy sản phẩm đang bật và còn hàng (trang bán công khai không đăng hàng đã hết)."""
    where, params = _products_where(q, available_only, service)
    for column, value in (("category_group", group), ("category", category)):
        if value:
            where += (" AND " if where else " WHERE ") + f"{column} = %s"
            params.append(value)
    sql = "SELECT * FROM public.account_products" + where + " ORDER BY created_at DESC, id DESC"
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        params += [limit, offset]
    rows = db.query(sql, tuple(params), fetchall=True)
    for row in rows:
        row["image_url"] = image_url(row.get("image") or "")
        row["service_label"] = SERVICES.get(row.get("service") or "", row.get("service") or "")
    return rows


def count_products(q: str = "", service: str = "") -> int:
    where, params = _products_where(q, False, service)
    return db.query("SELECT COUNT(*) AS n FROM public.account_products" + where, tuple(params), fetch=True)["n"]


def service_counts() -> dict[str, int]:
    rows = db.query("SELECT service, COUNT(*) AS n FROM public.account_products GROUP BY service", fetchall=True)
    return {row["service"]: row["n"] for row in rows}


def landing_items(service: str) -> list[dict[str, Any]]:
    """Sản phẩm đang bật của một trang dịch vụ, theo thứ tự nhập, ở dạng thẻ mà các trang landing dùng."""
    rows = db.query("""
        SELECT * FROM public.account_products WHERE service = %s AND active ORDER BY id
    """, (service,), fetchall=True)
    return [{
        "id": f"sp-{row['id']}",
        "raw_id": row["id"],
        "name": row["name"],
        "group": row["category_group"] or "",
        "grade": row["category"] or "",
        "icon": row["icon"] or "inventory_2",
        "desc": row["description"] or "",
        "specs": [line.strip() for line in (row["specs"] or "").splitlines() if line.strip()],
        "img": image_url(row["image"] or ""),
        "price": int(row["price"] or 0),
        "stock": int(row["stock"] or 0),
    } for row in rows]


def seed_landing_items(service: str, items: list[dict[str, Any]]) -> None:
    """Chép danh mục đang viết cố định trong code của một trang dịch vụ vào bảng (chạy một lần khi vừa thêm cột)."""
    label = SERVICES.get(service, service)
    for item in items:
        db.query("""
            INSERT INTO public.account_products
                (name, category_group, category, price, stock, instant, active, image, service, description, specs, icon)
            VALUES (%s, %s, %s, %s, %s, false, true, %s, %s, %s, %s, %s)
        """, (item["name"], item.get("group") or label, item.get("grade", ""),
              int(item.get("price") or 0), int(item.get("stock") or 0),
              item.get("img", ""), service,
              item.get("desc", ""), "\n".join(item.get("specs", [])), item.get("icon", "")))


def upgrade_placeholder_landing_items(service: str, items: list[dict[str, Any]], placeholder_names: set[str]) -> bool:
    """Nâng cấp danh mục mặc định chưa có giá (price=0) thành danh mục chi tiết có giá niêm yết.

    Chỉ chạy một lần khi toàn bộ dòng của `service` đều là mục giữ chỗ cũ giá 0đ;
    khi quản trị viên đã chỉnh giá hoặc thêm sản phẩm mới trong admin thì không ghi đè.
    """
    rows = db.query(
        "SELECT id, name, price FROM public.account_products WHERE service = %s ORDER BY id",
        (service,),
        fetchall=True,
    )
    if rows:
        all_generic_zero = all(
            int(row["price"] or 0) == 0 and (row["name"] or "").strip() in placeholder_names
            for row in rows
        )
        if not all_generic_zero:
            return False
        db.query("DELETE FROM public.account_products WHERE service = %s", (service,))
    seed_landing_items(service, items)
    return True


def category_tree() -> list[dict[str, Any]]:
    """Nhóm lớn > danh mục (thương hiệu) của các sản phẩm đang bán, kèm số sản phẩm; nhóm và danh mục đông nhất lên trước."""
    rows = db.query("""
        SELECT category_group, category, COUNT(*) AS n FROM public.account_products
        WHERE active AND stock > 0 AND service = %s GROUP BY category_group, category
    """, (ACCOUNT_SERVICE,), fetchall=True)
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = groups.setdefault(row["category_group"], {"name": row["category_group"], "count": 0, "brands": []})
        group["count"] += row["n"]
        if row["category"]:
            group["brands"].append({"name": row["category"], "count": row["n"]})
    for group in groups.values():
        group["brands"].sort(key=lambda brand: (-brand["count"], brand["name"]))
    return sorted(groups.values(), key=lambda group: (-group["count"], group["name"]))


def group_names() -> list[str]:
    rows = db.query("SELECT DISTINCT category_group FROM public.account_products ORDER BY category_group", fetchall=True)
    return [row["category_group"] for row in rows]


def get_product(product_id: int) -> dict[str, Any] | None:
    return db.query("SELECT * FROM public.account_products WHERE id = %s", (product_id,), fetch=True)


def save_product(product_id: int | None, *, name: str, category_group: str, category: str, price: int, stock: int,
                 instant: bool, active: bool, image: str = "", service: str = ACCOUNT_SERVICE,
                 description: str = "", specs: str = "", icon: str = "") -> None:
    """Thêm sản phẩm, hoặc sửa khi có `product_id`; `image` trống thì giữ ảnh cũ."""
    if service not in SERVICES:
        service = ACCOUNT_SERVICE
    if product_id:
        db.query("""
            UPDATE public.account_products
            SET name = %s, category_group = %s, category = %s, price = %s, stock = %s, instant = %s, active = %s,
                image = COALESCE(NULLIF(%s, ''), image), service = %s, description = %s, specs = %s, icon = %s
            WHERE id = %s
        """, (name, category_group, category, price, stock, instant, active, image, service, description, specs, icon,
              product_id))
    else:
        db.query("""
            INSERT INTO public.account_products
                (name, category_group, category, price, stock, instant, active, image, service, description, specs, icon)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (name, category_group, category, price, stock, instant, active, image, service, description, specs, icon))


def delete_product(product_id: int) -> None:
    db.query("DELETE FROM public.account_products WHERE id = %s", (product_id,))


def qr_url(amount: int, payment_code: str) -> str:
    """Ảnh VietQR của tài khoản nhận tiền, đã điền sẵn số tiền và nội dung chuyển khoản."""
    params = urlencode({"amount": str(int(amount)), "addInfo": payment_code, "accountName": PAY_ACCOUNT_NAME})
    return f"https://img.vietqr.io/image/{PAY_BANK_CODE}-{PAY_ACCOUNT}-qr_only.png?{params}"


def _order_serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("SESSION_SECRET is required to sign order tokens")
    return URLSafeTimedSerializer(secret, salt="accshop-order-v1")


def _sign_order_draft(draft: dict[str, Any]) -> str:
    return _order_serializer().dumps(draft)


def _verify_order_token(token: str, max_age: int = 30 * 24 * 3600) -> dict[str, Any] | None:
    if not token or not isinstance(token, str):
        return None
    try:
        data = _order_serializer().loads(token.strip(), max_age=max_age)
    except (BadSignature, SignatureExpired, Exception):
        return None
    if not isinstance(data, dict) or not data.get("code"):
        return None
    return data


def _prune_pending_drafts_locked(now_ts: float) -> None:
    expired = [k for k, v in _PENDING_DRAFTS.items() if now_ts - float(v.get("ts") or 0) > _DRAFT_TTL_SEC]
    for k in expired:
        _PENDING_DRAFTS.pop(k, None)
    if len(_PENDING_DRAFTS) > _DRAFT_MAX_ITEMS:
        oldest = sorted(_PENDING_DRAFTS.items(), key=lambda kv: float(kv[1].get("ts") or 0))
        for k, _ in oldest[: len(_PENDING_DRAFTS) - _DRAFT_MAX_ITEMS]:
            _PENDING_DRAFTS.pop(k, None)


def _get_pending_draft(code: str) -> dict[str, Any] | None:
    clean_code = (code or "").strip().upper()
    if not clean_code:
        return None
    now_ts = time.time()
    with _PENDING_LOCK:
        _prune_pending_drafts_locked(now_ts)
        draft = _PENDING_DRAFTS.get(clean_code)
        return dict(draft) if draft else None


def _extract_payment_codes(text: str) -> list[str]:
    normalized = re.sub(r"[^A-Za-z0-9]", " ", text or "").upper()
    return _PAYMENT_CODE_RE.findall(normalized)


def _generate_payment_code() -> str:
    for _ in range(20):
        code = "TK" + secrets.token_hex(3).upper()
        with _PENDING_LOCK:
            if code in _PENDING_DRAFTS:
                continue
        exists = db.query("SELECT 1 FROM public.account_orders WHERE payment_code = %s", (code,), fetch=True)
        if not exists:
            return code
    return "TK" + secrets.token_hex(3).upper()


def create_order(product_id: int, quantity: int, buyer_name: str, buyer_contact: str, chat_token: str) -> dict[str, Any]:
    """Khởi tạo phiên thanh toán QR ở phía người dùng (không ghi vào DB khi đơn chưa thanh toán)."""
    qty = int(quantity or 1)
    if qty < 1:
        raise HTTPException(status_code=400, detail="Số lượng không hợp lệ.")
    clean_name = (buyer_name or "").strip()
    clean_contact = (buyer_contact or "").strip()
    if not clean_name or not clean_contact:
        raise HTTPException(status_code=400, detail="Vui lòng nhập đầy đủ thông tin người mua.")
    product = get_product(product_id)
    svc = (product.get("service") or ACCOUNT_SERVICE) if product else ACCOUNT_SERVICE
    if (not product or svc != ACCOUNT_SERVICE
            or not product["active"] or product["stock"] < qty):
        raise HTTPException(status_code=409, detail="Sản phẩm tạm hết hàng hoặc không khả dụng.")

    unit_price = int(product["price"])
    total = unit_price * qty
    payment_code = _generate_payment_code()
    now_ts = int(time.time())
    draft = {
        "pid": int(product_id),
        "pname": str(product["name"]),
        "qty": qty,
        "price": unit_price,
        "svc": svc,
        "bname": clean_name,
        "bcontact": clean_contact,
        "ctoken": (chat_token or "").strip(),
        "code": payment_code,
        "ts": now_ts,
    }
    with _PENDING_LOCK:
        _prune_pending_drafts_locked(now_ts)
        _PENDING_DRAFTS[payment_code] = draft

    return {
        "id": None,
        "product_name": product["name"],
        "quantity": qty,
        "total": total,
        "payment_code": payment_code,
        "qr_url": qr_url(total, payment_code),
        "service": svc,
        "service_label": SERVICES.get(svc, svc),
        "token": _sign_order_draft(draft),
    }


def create_manual_order(
    *,
    service: str,
    product_id: int | None,
    product_name: str,
    quantity: int,
    unit_price: int,
    buyer_name: str,
    buyer_contact: str,
    status: str = "paid",
) -> int:
    """Quản trị viên tạo đơn hàng đã thanh toán cho một dịch vụ bất kỳ trong danh mục dịch vụ."""
    svc = service if service in SERVICES else ACCOUNT_SERVICE
    st = status if status in ADMIN_ORDER_STATUSES else "paid"
    qty = max(1, int(quantity or 1))
    price = max(0, int(unit_price or 0))
    pid = int(product_id) if product_id else None
    pname = product_name.strip()
    if pid:
        prod = get_product(pid)
        if prod:
            if not pname:
                pname = prod["name"]
            if price == 0 and int(prod.get("price") or 0) > 0:
                price = int(prod["price"])
            if (prod.get("service") or ACCOUNT_SERVICE) != svc:
                pid = None
        else:
            pid = None
    pname = pname or SERVICES.get(svc, "Đơn hàng dịch vụ")
    payment_code = _generate_payment_code()
    paid_amount = price * qty
    order_id = db.query("""
        INSERT INTO public.account_orders
            (product_id, product_name, quantity, unit_price, buyer_name, buyer_contact,
             status, payment_code, paid_amount, paid_at, chat_session_token, service)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), '', %s)
        RETURNING id
    """, (pid, pname, qty, price, buyer_name.strip(), buyer_contact.strip(),
          st, payment_code, paid_amount, svc),
        return_id=True)
    if st == "delivered" and pid and svc == ACCOUNT_SERVICE:
        db.query("UPDATE public.account_products SET stock = GREATEST(stock - %s, 0) WHERE id = %s", (qty, pid))
    return order_id


def is_paid(payment_code: str) -> bool:
    clean_code = (payment_code or "").strip().upper()
    if not clean_code:
        return False
    row = db.query("SELECT status FROM public.account_orders WHERE payment_code = %s", (clean_code,), fetch=True)
    return bool(row and row["status"] in PAID_STATUSES)


def unpaid_orders(codes: list[str]) -> list[dict[str, Any]]:
    """Các đơn cũ (tạo trước khi chuyển sang giỏ hàng client) còn chờ thanh toán trong DB."""
    if not codes:
        return []
    rows = db.query("""
        SELECT payment_code, product_name, quantity, unit_price, paid_amount FROM public.account_orders
        WHERE payment_code = ANY(%s) AND status = 'new' ORDER BY created_at DESC, id DESC
    """, (codes,), fetchall=True)
    for row in rows:
        row["due"] = int(row["unit_price"]) * row["quantity"] - int(row["paid_amount"])
        row["qr_url"] = qr_url(row["due"], row["payment_code"])
    return rows


def _finalize_draft_from_payments(draft: dict[str, Any]) -> dict[str, Any] | None:
    """Chốt đơn từ client draft khi tổng tiền chuyển khoản hợp lệ trong account_payments đủ thanh toán."""
    code = str(draft.get("code") or "").strip().upper()
    if not _PAYMENT_CODE_RE.fullmatch(code):
        return None
    qty = max(1, int(draft.get("qty") or 1))
    unit_price = max(0, int(draft.get("price") or 0))
    expected_total = unit_price * qty
    if expected_total <= 0:
        return None

    svc = draft.get("svc") if draft.get("svc") in SERVICES else ACCOUNT_SERVICE
    pid = int(draft["pid"]) if draft.get("pid") else None
    pname = str(draft.get("pname") or "").strip() or SERVICES.get(svc, "Đơn hàng dịch vụ")
    bname = str(draft.get("bname") or "").strip()
    bcontact = str(draft.get("bcontact") or "").strip()
    ctoken = str(draft.get("ctoken") or "").strip()

    created: dict[str, Any] | None = None
    with db.transaction() as cur:
        cur.execute(
            """
            SELECT sepay_id, amount, content
            FROM public.account_payments
            WHERE order_id IS NULL
              AND transfer_type = 'in'
              AND account_number = %s
              AND UPPER(content) LIKE %s
            ORDER BY sepay_id ASC
            FOR UPDATE
            """,
            (PAY_ACCOUNT, f"%{code}%"),
        )
        rows = cur.fetchall()
        matched = [row for row in rows if code in _extract_payment_codes(row.get("content") or "")]
        total_paid = sum(int(row.get("amount") or 0) for row in matched)
        if not matched or total_paid < expected_total:
            return None

        cur.execute(
            """
            INSERT INTO public.account_orders
                (product_id, product_name, quantity, unit_price, buyer_name, buyer_contact,
                 status, payment_code, paid_amount, paid_at, chat_session_token, service)
            VALUES (%s, %s, %s, %s, %s, %s, 'paid', %s, %s, now(), %s, %s)
            ON CONFLICT (payment_code) DO NOTHING
            RETURNING id, status, product_name, quantity, unit_price, paid_amount,
                      buyer_name, buyer_contact, payment_code, chat_session_token, service
            """,
            (pid, pname, qty, unit_price, bname, bcontact, code, total_paid, ctoken, svc),
        )
        row = cur.fetchone()
        if row:
            created = dict(row)
            sepay_ids = [int(item["sepay_id"]) for item in matched]
            cur.execute(
                "UPDATE public.account_payments SET order_id = %s WHERE sepay_id = ANY(%s) AND order_id IS NULL",
                (created["id"], sepay_ids),
            )

    if created:
        with _PENDING_LOCK:
            _PENDING_DRAFTS.pop(code, None)
    return created


def resolve_order_payment(code: str, token: str = "") -> tuple[bool, dict[str, Any] | None]:
    """Kiểm tra đơn đã thanh toán chưa; nếu tiền đã vào account_payments thì chốt đơn sang trạng thái paid."""
    clean_code = (code or "").strip().upper()
    if not clean_code:
        return False, None
    if is_paid(clean_code):
        return True, None
    draft: dict[str, Any] | None = None
    if token:
        verified = _verify_order_token(token)
        if verified and str(verified.get("code") or "").strip().upper() == clean_code:
            draft = verified
    if not draft:
        draft = _get_pending_draft(clean_code)
    if not draft:
        return False, None
    created = _finalize_draft_from_payments(draft)
    if created:
        return True, created
    return is_paid(clean_code), None


def record_payment(tx: dict[str, Any]) -> dict[str, Any] | None:
    """Ghi một giao dịch SePay (mỗi giao dịch chỉ ghi một lần) và chốt đơn khi nhận đủ tiền.
    Trả về đơn vừa được thanh toán đủ, hoặc None."""
    sepay_id = int(tx["id"])
    amount = int(tx.get("transferAmount") or 0)
    raw_content = str(tx.get("content") or "").strip()
    tx_code = str(tx.get("code") or "").strip()
    stored_content = (
        f"{raw_content} {tx_code}".strip()
        if tx_code and tx_code.upper() not in raw_content.upper()
        else raw_content
    )
    inserted = db.query("""
        INSERT INTO public.account_payments (sepay_id, amount, transfer_type, account_number, content, reference_code, transaction_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (sepay_id) DO NOTHING
    """, (sepay_id, amount, tx.get("transferType") or "", str(tx.get("accountNumber") or ""),
          stored_content, str(tx.get("referenceCode") or ""), str(tx.get("transactionDate") or "")))
    if not inserted or tx.get("transferType") != "in" or str(tx.get("accountNumber") or "") != PAY_ACCOUNT:
        return None
    codes = _extract_payment_codes(stored_content)
    if not codes:
        return None
    payment_code = codes[0]
    order = db.query("""
        UPDATE public.account_orders
        SET paid_amount = paid_amount + %s, paid_at = now(),
            status = CASE WHEN status = 'new' AND paid_amount + %s >= unit_price * quantity THEN 'paid' ELSE status END
        WHERE payment_code = %s
        RETURNING id, status, product_name, quantity, unit_price, paid_amount, buyer_name, buyer_contact,
                  payment_code, chat_session_token, service
    """, (amount, amount, payment_code), fetch=True)
    if order:
        db.query("UPDATE public.account_payments SET order_id = %s WHERE sepay_id = %s", (order["id"], sepay_id))
        return order if order["status"] == "paid" else None

    draft = _get_pending_draft(payment_code)
    if not draft:
        return None
    return _finalize_draft_from_payments(draft)


def _orders_where(status: str = "", service: str = "", q: str = "") -> tuple[str, list]:
    """Trang quản trị chỉ hiển thị các đơn đã thanh toán (`paid`, `delivered`)."""
    where: list[str] = []
    params: list[Any] = []
    if status and status in PAID_STATUSES:
        where.append("status = %s")
        params.append(status)
    else:
        where.append("status = ANY(%s)")
        params.append(list(PAID_STATUSES))
    if service and service in SERVICES:
        where.append("service = %s")
        params.append(service)
    clean_q = (q or "").strip()
    if clean_q:
        q_num = clean_q.lstrip("#")
        like = f"%{clean_q}%"
        where.append(
            "(id::text = %s OR product_name ILIKE %s OR buyer_name ILIKE %s "
            "OR buyer_contact ILIKE %s OR payment_code ILIKE %s)"
        )
        params += [q_num, like, like, like, like]
    return " WHERE " + " AND ".join(where), params


def list_orders(status: str = "", limit: int = 20, offset: int = 0, service: str = "", q: str = "") -> list[dict[str, Any]]:
    where, params = _orders_where(status=status, service=service, q=q)
    rows = db.query("SELECT * FROM public.account_orders" + where + " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    tuple(params + [limit, offset]), fetchall=True)
    for row in rows:
        svc = row.get("service") or ACCOUNT_SERVICE
        row["service"] = svc
        row["service_label"] = SERVICES.get(svc, svc)
        row["created_local"] = row["created_at"].astimezone()
        row["total"] = int(row["unit_price"]) * row["quantity"]
    return rows


def count_orders(status: str = "", service: str = "", q: str = "") -> int:
    where, params = _orders_where(status=status, service=service, q=q)
    return db.query("SELECT COUNT(*) AS n FROM public.account_orders" + where, tuple(params), fetch=True)["n"]


def order_service_counts(status: str = "", q: str = "") -> dict[str, int]:
    """Đếm số đơn đã thanh toán theo từng dịch vụ (giữ bộ lọc trạng thái và từ khóa hiện tại nếu có)."""
    where, params = _orders_where(status=status, service="", q=q)
    rows = db.query(
        "SELECT COALESCE(NULLIF(service, ''), %s) AS service, COUNT(*) AS n FROM public.account_orders"
        + where
        + " GROUP BY COALESCE(NULLIF(service, ''), %s)",
        tuple([ACCOUNT_SERVICE] + params + [ACCOUNT_SERVICE]),
        fetchall=True,
    )
    return {row["service"]: row["n"] for row in rows}


def set_order_status(order_id: int, status: str) -> None:
    """Đổi trạng thái đơn đã thanh toán (Đã thanh toán / Đã giao); kho trừ khi chuyển sang Đã giao."""
    if status not in ADMIN_ORDER_STATUSES:
        return
    order = db.query("SELECT * FROM public.account_orders WHERE id = %s", (order_id,), fetch=True)
    if not order or order["status"] == status:
        return
    pid = order.get("product_id")
    svc = order.get("service") or ACCOUNT_SERVICE
    if pid and svc == ACCOUNT_SERVICE:
        if status == "delivered":
            db.query("UPDATE public.account_products SET stock = GREATEST(stock - %s, 0) WHERE id = %s",
                     (order["quantity"], pid))
        elif order["status"] == "delivered":
            db.query("UPDATE public.account_products SET stock = stock + %s WHERE id = %s",
                     (order["quantity"], pid))
    db.query("UPDATE public.account_orders SET status = %s WHERE id = %s", (status, order_id))
