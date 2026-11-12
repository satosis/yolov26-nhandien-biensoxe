"""Sổ quy cách hàng hóa (Bảng bazem thép hộp, bazem I/H, quy cách thép hình, ảnh catalogue)."""

from __future__ import annotations

from decimal import Decimal
import io
from pathlib import Path
from typing import Any
from uuid import uuid4

import openpyxl
from PIL import Image, ImageOps, UnidentifiedImageError

from backend.services import config as cfg
from backend.services.database import db
from frontend.formatting import format_number
from frontend.modules.enterprise.sales_service import fold_text

SECTIONS = {
    "box": "Bảng BAZEM thép hộp",
    "shape": "BAZEM I, H",
    "channel": "Quy cách thép hình",
}

PHOTO_DIR = Path(cfg.GOODS_SPEC_DIR)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        try:
            return Decimal(str(value))
        except Exception:
            raise ValueError(f"Số không hợp lệ: {value}")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if "." in s and "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            return Decimal(s)
        except Exception:
            raise ValueError(f"Số không hợp lệ: {value}")
    raise ValueError(f"Số không hợp lệ: {value}")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return str(value).strip()


def spec_book(company_id: int) -> dict:
    # 1. Box specs
    box_rows_data = db._execute_query(
        """
        SELECT id, section, name, bundle_qty, weight_text, sort_order
        FROM goods_specs
        WHERE company_id = %s AND section = 'box'
        ORDER BY sort_order, id
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []

    thickness_rows = db._execute_query(
        """
        SELECT DISTINCT w.thickness
        FROM goods_spec_weights w
        JOIN goods_specs s ON w.spec_id = s.id
        WHERE s.company_id = %s AND s.section = 'box'
        ORDER BY w.thickness
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []
    thicknesses: list[Decimal] = [Decimal(str(r["thickness"])) for r in thickness_rows]

    weights_by_spec: dict[int, dict[Decimal, Decimal]] = {}
    if box_rows_data:
        spec_ids = tuple(r["id"] for r in box_rows_data)
        weight_rows = db._execute_query(
            """
            SELECT spec_id, thickness, weight
            FROM goods_spec_weights
            WHERE spec_id IN %s
            ORDER BY thickness
            """,
            (spec_ids,),
            fetchall=True,
            commit=False,
        ) or []
        for w in weight_rows:
            weights_by_spec.setdefault(w["spec_id"], {})[Decimal(str(w["thickness"]))] = Decimal(str(w["weight"]))

    lookup: list[dict] = []
    box_rows = []
    for r in box_rows_data:
        sid = r["id"]
        sw = weights_by_spec.get(sid, {})
        for t, w in sorted(sw.items(), key=lambda x: x[0]):
            lookup.append({"name": r["name"], "thickness": float(t), "weight": float(w)})
        cells = [sw.get(t) for t in thicknesses]
        edit_weights = [
            {"thickness": format_number(t), "weight": format_number(w)}
            for t, w in sorted(sw.items(), key=lambda x: x[0])
        ]
        box_rows.append({
            "id": sid,
            "name": r["name"],
            "bundle_qty": r["bundle_qty"] or "",
            "cells": cells,
            "edit": {
                "id": sid,
                "section": "box",
                "name": r["name"],
                "bundle_qty": r["bundle_qty"] or "",
                "weight_text": r["weight_text"] or "",
                "spec_text": "",
                "weights": edit_weights,
            },
        })

    # 2. Shape specs
    shape_rows_data = db._execute_query(
        """
        SELECT id, name, weight_text, spec_text, sort_order
        FROM goods_specs
        WHERE company_id = %s AND section = 'shape'
        ORDER BY sort_order, id
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []
    shape_rows = [
        {
            "id": r["id"],
            "name": r["name"],
            "weight_text": r["weight_text"] or "",
            "spec_text": r["spec_text"] or "",
            "edit": {
                "id": r["id"],
                "section": "shape",
                "name": r["name"],
                "bundle_qty": "",
                "weight_text": r["weight_text"] or "",
                "spec_text": r["spec_text"] or "",
                "weights": [],
            },
        }
        for r in shape_rows_data
    ]

    # 3. Channel specs
    channel_rows_data = db._execute_query(
        """
        SELECT id, name, sort_order
        FROM goods_specs
        WHERE company_id = %s AND section = 'channel'
        ORDER BY sort_order, id
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []
    channel_rows = [
        {
            "id": r["id"],
            "name": r["name"],
            "edit": {
                "id": r["id"],
                "section": "channel",
                "name": r["name"],
                "bundle_qty": "",
                "weight_text": "",
                "spec_text": "",
                "weights": [],
            },
        }
        for r in channel_rows_data
    ]

    # 4. Photos
    photo_rows_data = db._execute_query(
        """
        SELECT id, caption, file_name, sort_order
        FROM goods_spec_photos
        WHERE company_id = %s
        ORDER BY sort_order, id
        """,
        (company_id,),
        fetchall=True,
        commit=False,
    ) or []
    photo_rows = [
        {
            "id": r["id"],
            "caption": r["caption"] or "",
            "url": f"/upload/goods_specs/{r['file_name']}",
        }
        for r in photo_rows_data
    ]

    return {
        "box": {
            "thicknesses": thicknesses,
            "rows": box_rows,
            "lookup": lookup,
        },
        "shape": shape_rows,
        "channel": channel_rows,
        "photos": photo_rows,
    }


def lookup_data(company_id: int) -> dict:
    book = spec_book(company_id)
    return {
        "box": book["box"]["lookup"],
        "shape": [{"name": r["name"], "weight_text": r["weight_text"], "spec_text": r["spec_text"]} for r in book["shape"]],
    }


def save_spec(company_id: int, data: dict, spec_id: int | None = None) -> dict:
    section = str(data.get("section") or "").strip()
    if section not in SECTIONS:
        raise ValueError("Nhóm không hợp lệ.")
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Vui lòng nhập tên hàng.")
    bundle_qty = str(data.get("bundle_qty") or "").strip()
    weight_text = str(data.get("weight_text") or "").strip()
    spec_text = str(data.get("spec_text") or "").strip() if section == "shape" else ""

    parsed_weights: list[tuple[Decimal, Decimal]] = []
    if section == "box":
        raw_weights = data.get("weights") or []
        seen_thicknesses: set[Decimal] = set()
        for item in raw_weights:
            if not isinstance(item, dict):
                continue
            t_raw = item.get("thickness")
            w_raw = item.get("weight")
            t_str = "" if t_raw is None else str(t_raw).strip()
            w_str = "" if w_raw is None else str(w_raw).strip()
            if not t_str and not w_str:
                continue
            if w_str and not t_str:
                raise ValueError("Vui lòng nhập độ dày.")
            if not w_str:
                continue
            t = _decimal(t_raw)
            if t is None:
                raise ValueError("Vui lòng nhập độ dày.")
            w = _decimal(w_raw)
            if w is None:
                continue
            if t in seen_thicknesses:
                raise ValueError(f"Độ dày {format_number(t)} bị trùng.")
            seen_thicknesses.add(t)
            parsed_weights.append((t, w))

    with db.transaction() as cur:
        if spec_id:
            cur.execute(
                """
                UPDATE goods_specs
                SET section = %s, name = %s, bundle_qty = %s, weight_text = %s, spec_text = %s
                WHERE id = %s AND company_id = %s
                RETURNING id
                """,
                (section, name, bundle_qty, weight_text, spec_text, spec_id, company_id),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Không tìm thấy quy cách.")
            target_id = row["id"]
        else:
            cur.execute(
                """
                SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order
                FROM goods_specs
                WHERE company_id = %s AND section = %s
                """,
                (company_id, section),
            )
            next_order = cur.fetchone()["next_order"]
            cur.execute(
                """
                INSERT INTO goods_specs (company_id, section, name, bundle_qty, weight_text, spec_text, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (company_id, section, name, bundle_qty, weight_text, spec_text, next_order),
            )
            target_id = cur.fetchone()["id"]

        cur.execute("DELETE FROM goods_spec_weights WHERE spec_id = %s", (target_id,))
        if section == "box":
            for t, w in parsed_weights:
                cur.execute(
                    """
                    INSERT INTO goods_spec_weights (spec_id, thickness, weight)
                    VALUES (%s, %s, %s)
                    """,
                    (target_id, t, w),
                )

    return {"id": target_id}


def delete_spec(company_id: int, spec_id: int) -> bool:
    with db.transaction() as cur:
        cur.execute(
            "DELETE FROM goods_specs WHERE id = %s AND company_id = %s RETURNING id",
            (spec_id, company_id),
        )
        row = cur.fetchone()
        return bool(row)


def _store_photo(content: bytes) -> str:
    try:
        img = Image.open(io.BytesIO(content))
    except (UnidentifiedImageError, Exception):
        raise ValueError("Tệp không phải ảnh.")
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    img.thumbnail((2000, 2000))
    file_name = f"{uuid4().hex}.jpg"
    img.save(PHOTO_DIR / file_name, format="JPEG", quality=85)
    return file_name


def add_photo(company_id: int, caption: str, content: bytes) -> dict:
    file_name = _store_photo(content)
    caption_clean = (caption or "").strip()
    with db.transaction() as cur:
        cur.execute(
            """
            SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order
            FROM goods_spec_photos
            WHERE company_id = %s
            """,
            (company_id,),
        )
        next_order = cur.fetchone()["next_order"]
        cur.execute(
            """
            INSERT INTO goods_spec_photos (company_id, caption, file_name, sort_order)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (company_id, caption_clean, file_name, next_order),
        )
        row = cur.fetchone()
        photo_id = row["id"]
    return {"id": photo_id, "caption": caption_clean, "url": f"/upload/goods_specs/{file_name}"}


def update_photo(company_id: int, photo_id: int, caption: str) -> bool:
    caption_clean = (caption or "").strip()
    with db.transaction() as cur:
        cur.execute(
            """
            UPDATE goods_spec_photos
            SET caption = %s
            WHERE id = %s AND company_id = %s
            RETURNING id
            """,
            (caption_clean, photo_id, company_id),
        )
        row = cur.fetchone()
        return bool(row)


def delete_photo(company_id: int, photo_id: int) -> bool:
    with db.transaction() as cur:
        cur.execute(
            """
            DELETE FROM goods_spec_photos
            WHERE id = %s AND company_id = %s
            RETURNING file_name
            """,
            (photo_id, company_id),
        )
        row = cur.fetchone()
    if row:
        (PHOTO_DIR / row["file_name"]).unlink(missing_ok=True)
        return True
    return False


def parse_workbook(file_bytes: bytes) -> dict:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    all_boxes: list[dict] = []
    all_shapes: list[dict] = []
    all_channels: list[dict] = []
    all_photos: list[dict] = []

    for sheetname in wb.sheetnames:
        ws = wb[sheetname]

        def fold(v: Any) -> str:
            return " ".join(fold_text(str(v)).split())

        used: set[tuple[int, int]] = set()

        # a. Thép hộp
        hr, nc = None, None
        for r in range(1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                val = ws.cell(r, c).value
                if val is not None and fold(val) == "do day":
                    hr, nc = r, c
                    break
            if hr is not None:
                break

        if hr is not None and nc is not None:
            used.add((hr, nc))
            bundle_col = None
            thickness_cols: list[tuple[int, Decimal]] = []
            for c in range(nc + 1, ws.max_column + 1):
                val = ws.cell(hr, c).value
                if val is None or str(val).strip() == "":
                    if thickness_cols:
                        break
                    continue
                f_val = fold(val)
                if f_val.startswith("so cay"):
                    bundle_col = c
                    used.add((hr, c))
                else:
                    try:
                        t = _decimal(val)
                        if t is not None:
                            thickness_cols.append((c, t))
                            used.add((hr, c))
                        else:
                            if thickness_cols:
                                break
                    except Exception:
                        if thickness_cols:
                            break

            box_rows_in_sheet: list[dict] = []
            for r in range(hr + 1, ws.max_row + 1):
                name_val = ws.cell(r, nc).value
                if name_val is None or str(name_val).strip() == "":
                    if box_rows_in_sheet:
                        break
                    continue
                used.add((r, nc))
                if fold(name_val) == "kich thuoc":
                    continue
                name_str = _text(name_val)
                bundle_str = ""
                if bundle_col:
                    bundle_str = _text(ws.cell(r, bundle_col).value)
                    used.add((r, bundle_col))
                row_weights: list[dict] = []
                for c, th in thickness_cols:
                    cell_val = ws.cell(r, c).value
                    used.add((r, c))
                    if cell_val is not None and str(cell_val).strip() != "":
                        try:
                            w = _decimal(cell_val)
                            if w is not None:
                                row_weights.append({"thickness": th, "weight": w})
                        except Exception:
                            pass
                box_rows_in_sheet.append({
                    "name": name_str,
                    "bundle_qty": bundle_str,
                    "weights": row_weights,
                })
            all_boxes.extend(box_rows_in_sheet)

        # b. Thép hình
        hr_b, hc_b = None, None
        for r in range(1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                val = ws.cell(r, c).value
                if val is not None and fold(val) == "ten hang":
                    hr_b, hc_b = r, c
                    break
            if hr_b is not None:
                break

        if hr_b is not None and hc_b is not None:
            used.add((hr_b, hc_b))
            used.add((hr_b, hc_b + 1))
            spec_col = hc_b + 2 if fold(ws.cell(hr_b, hc_b + 2).value or "") == "quy cach" else None
            if spec_col:
                used.add((hr_b, spec_col))
            for r in range(hr_b + 1, ws.max_row + 1):
                val_name = ws.cell(r, hc_b).value
                if val_name is not None and str(val_name).strip() != "":
                    name_str = _text(val_name)
                    weight_str = _text(ws.cell(r, hc_b + 1).value)
                    spec_str = _text(ws.cell(r, spec_col).value) if spec_col else ""
                    used.add((r, hc_b))
                    used.add((r, hc_b + 1))
                    if spec_col:
                        used.add((r, spec_col))
                    all_shapes.append({
                        "name": name_str,
                        "weight_text": weight_str,
                        "spec_text": spec_str,
                    })

        # c. Quy cách
        hr_c, hc_c = None, None
        for r in range(1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                val = ws.cell(r, c).value
                if val is not None and fold(val) == "quy cach thep hinh":
                    hr_c, hc_c = r, c
                    break
            if hr_c is not None:
                break

        if hr_c is not None and hc_c is not None:
            used.add((hr_c, hc_c))
            for r in range(hr_c + 1, ws.max_row + 1):
                val_name = ws.cell(r, hc_c).value
                if val_name is not None and str(val_name).strip() != "":
                    name_str = _text(val_name)
                    used.add((r, hc_c))
                    all_channels.append({
                        "name": name_str,
                    })

        # d. Ảnh
        sheet_images = getattr(ws, "_images", [])
        for img in sorted(sheet_images, key=lambda i: (i.anchor._from.row, i.anchor._from.col)):
            fr = img.anchor._from.row + 1
            fc = img.anchor._from.col + 1
            tr = img.anchor.to.row + 1 if hasattr(img.anchor, "to") and img.anchor.to else fr
            tc = img.anchor.to.col + 1 if hasattr(img.anchor, "to") and img.anchor.to else fc
            caption = ""
            found_caption = False
            for r in range(fr, tr + 1):
                for c in range(1, tc + 1):
                    if (r, c) in used:
                        continue
                    val = ws.cell(r, c).value
                    if val is not None:
                        if isinstance(val, (int, float, Decimal)) and not isinstance(val, bool):
                            continue
                        s = str(val).strip()
                        if not s:
                            continue
                        caption = s
                        found_caption = True
                        break
                if found_caption:
                    break
            all_photos.append({
                "caption": caption,
                "data": img._data(),
            })

    return {
        "box": all_boxes,
        "shape": all_shapes,
        "channel": all_channels,
        "photos": all_photos,
    }


def import_excel(company_id: int, file_bytes: bytes) -> dict:
    parsed = parse_workbook(file_bytes)
    all_boxes = parsed["box"]
    all_shapes = parsed["shape"]
    all_channels = parsed["channel"]
    all_photos = parsed["photos"]

    if not (all_boxes or all_shapes or all_channels or all_photos):
        raise ValueError("Không tìm thấy dữ liệu quy cách trong file.")

    stored_photos: list[tuple[str, str]] = []
    for p in all_photos:
        fn = _store_photo(p["data"])
        stored_photos.append((p["caption"], fn))

    with db.transaction() as cur:
        cur.execute("SELECT file_name FROM goods_spec_photos WHERE company_id = %s", (company_id,))
        old_photo_files = [r["file_name"] for r in (cur.fetchall() or [])]

        cur.execute("DELETE FROM goods_specs WHERE company_id = %s", (company_id,))
        cur.execute("DELETE FROM goods_spec_photos WHERE company_id = %s", (company_id,))

        for idx, item in enumerate(all_boxes, start=1):
            cur.execute(
                """
                INSERT INTO goods_specs (company_id, section, name, bundle_qty, weight_text, sort_order)
                VALUES (%s, 'box', %s, %s, '', %s)
                RETURNING id
                """,
                (company_id, item["name"], item["bundle_qty"], idx),
            )
            spec_id = cur.fetchone()["id"]
            for w in item["weights"]:
                cur.execute(
                    """
                    INSERT INTO goods_spec_weights (spec_id, thickness, weight)
                    VALUES (%s, %s, %s)
                    """,
                    (spec_id, w["thickness"], w["weight"]),
                )

        for idx, item in enumerate(all_shapes, start=1):
            cur.execute(
                """
                INSERT INTO goods_specs (company_id, section, name, bundle_qty, weight_text, spec_text, sort_order)
                VALUES (%s, 'shape', %s, '', %s, %s, %s)
                """,
                (company_id, item["name"], item["weight_text"], item.get("spec_text", ""), idx),
            )

        for idx, item in enumerate(all_channels, start=1):
            cur.execute(
                """
                INSERT INTO goods_specs (company_id, section, name, bundle_qty, weight_text, sort_order)
                VALUES (%s, 'channel', %s, '', '', %s)
                """,
                (company_id, item["name"], idx),
            )

        for idx, (caption, file_name) in enumerate(stored_photos, start=1):
            cur.execute(
                """
                INSERT INTO goods_spec_photos (company_id, caption, file_name, sort_order)
                VALUES (%s, %s, %s, %s)
                """,
                (company_id, caption, file_name, idx),
            )

    for fn in old_photo_files:
        (PHOTO_DIR / fn).unlink(missing_ok=True)

    return {
        "box": len(all_boxes),
        "shape": len(all_shapes),
        "channel": len(all_channels),
        "photos": len(stored_photos),
    }
