"""Financial Notes (Bản thuyết minh Báo cáo tài chính - Mẫu B09-DNN) service for Dai Lo Xanh."""

from __future__ import annotations

from datetime import date
from io import BytesIO
import logging
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from backend.services.database import db
from frontend.context.company_context import get_current_company_id
from frontend.modules.transport.balance_sheet_service import (
    _net_map,
    _short_term_242,
    build_balance_sheet_values,
)
from frontend.modules.transport.depreciation_service import get_depreciation_data
from frontend.modules.transport.income_statement_service import (
    _shift_year_back,
    build_income_statement,
)
from frontend.modules.transport.trial_balance_service import get_trial_balance_data

logger = logging.getLogger(__name__)


def _format_number_vn(val: int | float | None) -> str:
    """Format an integer with dot thousand separator."""
    if val is None:
        return "0"
    return f"{int(round(val)):,}".replace(",", ".")


def build_notes_blocks(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Hàm thuần tính toán và xây dựng danh sách các khối của Thuyết minh BCTC (Mẫu B09-DNN)."""
    company = ctx.get("company") or {}
    company_name = company.get("name") or "CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH"
    business_activity = company.get("business_activity") or "Vận tải hàng hóa bằng đường bộ"

    year = ctx.get("year", 2026)
    date_from = ctx.get("date_from") or date(year, 1, 1)
    date_to = ctx.get("date_to") or date(year, 12, 31)

    tb_cur = ctx.get("tb_cur") or {}
    tb_prev = ctx.get("tb_prev") or {}
    bs_cur = ctx.get("bs_cur") or {}
    bs_open = ctx.get("bs_open") or {}
    is_cur = ctx.get("is_cur") or {}
    is_prev = ctx.get("is_prev") or {}
    dep_rows = ctx.get("dep_rows") or []
    staff_count = ctx.get("staff_count")
    short_242 = ctx.get("short_242")

    is_full_year = (date_from == date(year, 1, 1) and date_to == date(year, 12, 31))
    period_text = f"Năm {year}" if is_full_year else f"Từ ngày 01/01/{year} đến ngày {date_to.strftime('%d/%m/%Y')}"

    blocks: list[dict[str, Any]] = []

    # TIÊU ĐỀ
    blocks.append({
        "type": "title",
        "text": "BẢN THUYẾT MINH BÁO CÁO TÀI CHÍNH",
    })
    blocks.append({
        "type": "subheading",
        "text": period_text,
        "align": "center",
        "italic": True,
    })

    # I. ĐẶC ĐIỂM HOẠT ĐỘNG CỦA DOANH NGHIỆP
    blocks.append({
        "type": "heading",
        "text": "I. ĐẶC ĐIỂM HOẠT ĐỘNG CỦA DOANH NGHIỆP",
    })

    # 1. Hình thức sở hữu vốn
    c_upper = company_name.upper()
    if "TNHH" in c_upper:
        form_name = "Công ty trách nhiệm hữu hạn"
    elif "CỔ PHẦN" in c_upper or "CP" in c_upper:
        form_name = "Công ty cổ phần"
    else:
        form_name = company_name
    von_511 = bs_cur.get("511", 0)
    d_to_str = date_to.strftime("%d/%m/%Y")
    blocks.append({
        "type": "para",
        "text": f"1. Hình thức sở hữu vốn: {form_name}. Vốn góp của chủ sở hữu tại ngày {d_to_str}: {_format_number_vn(von_511)} đồng.",
    })

    # 2. Lĩnh vực kinh doanh
    blocks.append({
        "type": "para",
        "text": "2. Lĩnh vực kinh doanh: Dịch vụ vận tải.",
    })

    # 3. Ngành nghề kinh doanh
    act_str = business_activity.strip() if business_activity else "Vận tải hàng hóa"
    blocks.append({
        "type": "para",
        "text": f"3. Ngành nghề kinh doanh: {act_str}.",
    })

    # 4. Chu kỳ sản xuất, kinh doanh thông thường
    blocks.append({
        "type": "para",
        "text": "4. Chu kỳ sản xuất, kinh doanh thông thường: Không quá 12 tháng.",
    })

    # 5. Cấu trúc doanh nghiệp
    cur_leaf_rows = [r for r in (tb_cur.get("all_rows") or tb_cur.get("rows") or []) if not r.get("is_parent")]
    invest_items: list[str] = []
    for r in cur_leaf_rows:
        code = str(r.get("code") or "").strip()
        if code.startswith(("221", "222", "228")):
            net_val = int(round(float(r.get("close_dr", 0)) - float(r.get("close_cr", 0))))
            if net_val != 0:
                invest_items.append(f"{code} - {r.get('name')}: {_format_number_vn(net_val)} đồng")
    if not invest_items:
        blocks.append({
            "type": "para",
            "text": "5. Cấu trúc doanh nghiệp: Công ty không có khoản đầu tư vào công ty con, công ty liên doanh, liên kết.",
        })
    else:
        blocks.append({
            "type": "para",
            "text": f"5. Cấu trúc doanh nghiệp: Các khoản đầu tư: {', '.join(invest_items)}.",
        })

    # 6. Khả năng so sánh thông tin
    blocks.append({
        "type": "para",
        "text": "6. Khả năng so sánh thông tin: Thông tin trên báo cáo tài chính được lập nhất quán giữa các kỳ nên có khả năng so sánh.",
    })

    # 7. Nhân viên
    if staff_count is not None:
        blocks.append({
            "type": "para",
            "text": f"7. Nhân viên: Tổng số lao động của Công ty tại ngày {d_to_str}: {staff_count} người.",
        })

    # II. KỲ KẾ TOÁN, ĐƠN VỊ TIỀN TỆ SỬ DỤNG TRONG KẾ TOÁN
    blocks.append({
        "type": "heading",
        "text": "II. KỲ KẾ TOÁN, ĐƠN VỊ TIỀN TỆ SỬ DỤNG TRONG KẾ TOÁN",
    })
    blocks.append({
        "type": "para",
        "text": "1. Kỳ kế toán năm: Bắt đầu từ ngày 01/01 và kết thúc vào ngày 31/12 hằng năm.",
    })
    blocks.append({
        "type": "para",
        "text": "2. Đơn vị tiền tệ sử dụng trong kế toán: Đồng Việt Nam (VND).",
    })

    # III. CHUẨN MỰC VÀ CHẾ ĐỘ KẾ TOÁN ÁP DỤNG
    blocks.append({
        "type": "heading",
        "text": "III. CHUẨN MỰC VÀ CHẾ ĐỘ KẾ TOÁN ÁP DỤNG",
    })
    blocks.append({
        "type": "para",
        "text": "1. Chế độ kế toán áp dụng: Thông tư số 133/2016/TT-BTC ngày 26/8/2016 của Bộ Tài chính.",
    })
    blocks.append({
        "type": "para",
        "text": "2. Tuyên bố về việc tuân thủ Chuẩn mực và Chế độ kế toán: Báo cáo tài chính được lập và trình bày phù hợp với các Chuẩn mực kế toán Việt Nam và Chế độ kế toán doanh nghiệp nhỏ và vừa hiện hành.",
    })

    # IV. CÁC CHÍNH SÁCH KẾ TOÁN ÁP DỤNG
    blocks.append({
        "type": "heading",
        "text": "IV. CÁC CHÍNH SÁCH KẾ TOÁN ÁP DỤNG",
    })
    blocks.append({
        "type": "para",
        "text": "1. Nguyên tắc ghi nhận tiền: Tiền mặt, tiền gửi ngân hàng được ghi nhận theo đồng Việt Nam (VND). Các giao dịch bằng ngoại tệ (nếu có) được quy đổi theo tỷ giá giao dịch thực tế tại thời điểm phát sinh.",
    })
    blocks.append({
        "type": "para",
        "text": "2. Nguyên tắc ghi nhận các khoản nợ phải thu: Nợ phải thu được theo dõi chi tiết theo từng đối tượng khách hàng và đối tượng khác, ghi nhận theo giá trị hóa đơn đã xuất và chứng từ hợp lệ.",
    })
    blocks.append({
        "type": "para",
        "text": "3. Nguyên tắc ghi nhận công cụ, dụng cụ: CCDC được ghi nhận theo giá mua thực tế, phân bổ dần vào chi phí theo phương pháp đường thẳng theo thời gian sử dụng ước tính, phản ánh qua TK 242.",
    })

    # 4. TSCĐ
    dep_types: dict[str, list[int]] = {}
    for r in dep_rows:
        at = (r.get("asset_type") or "TSCĐ khác").strip()
        m = int(r.get("usage_months_total") or 0)
        if m > 0:
            dep_types.setdefault(at, []).append(m)
    dep_policy_lines = [
        "4. Nguyên tắc ghi nhận và khấu hao tài sản cố định:",
        "- Nguyên giá TSCĐ bao gồm giá mua và các chi phí liên quan trực tiếp đến việc đưa tài sản vào trạng thái sẵn sàng sử dụng.",
        "- Khấu hao được trích theo phương pháp đường thẳng theo quy định tại Thông tư 45/2013/TT-BTC.",
    ]
    if dep_types:
        dep_policy_lines.append("- Thời gian trích khấu hao ước tính theo từng loại tài sản:")
        for at, months_list in sorted(dep_types.items()):
            min_m = min(months_list)
            max_m = max(months_list)
            if min_m == max_m:
                dep_policy_lines.append(f"  + {at}: {min_m} tháng")
            else:
                dep_policy_lines.append(f"  + {at}: từ {min_m} đến {max_m} tháng")
    else:
        dep_policy_lines.append("- Thời gian trích khấu hao: Phương tiện vận tải: từ 72 đến 120 tháng; Thiết bị quản lý: từ 36 đến 60 tháng.")
    blocks.append({
        "type": "para",
        "text": "\n".join(dep_policy_lines),
    })

    blocks.append({
        "type": "para",
        "text": "5. Nguyên tắc ghi nhận chi phí trả trước: Chi phí trả trước được phân bổ dần vào chi phí hoạt động theo phương pháp đường thẳng tương ứng với thời gian đem lại lợi ích kinh tế.",
    })
    blocks.append({
        "type": "para",
        "text": "6. Nguyên tắc ghi nhận nợ phải trả: Nợ phải trả được theo dõi chi tiết theo từng nhà cung cấp và nội dung khoản phải trả.",
    })
    blocks.append({
        "type": "para",
        "text": "7. Nguyên tắc ghi nhận vốn chủ sở hữu: Vốn góp của chủ sở hữu được ghi nhận theo số vốn thực tế đã góp. Lợi nhuận sau thuế chưa phân phối phản ánh kết quả hoạt động kinh doanh sau khi trừ chi phí thuế TNDN.",
    })
    blocks.append({
        "type": "para",
        "text": "8. Nguyên tắc ghi nhận doanh thu: Doanh thu cung cấp dịch vụ được ghi nhận khi dịch vụ vận tải đã hoàn thành, khối lượng được nghiệm thu và đã lập hóa đơn.",
    })
    blocks.append({
        "type": "para",
        "text": "9. Nguyên tắc ghi nhận giá vốn hàng bán: Giá vốn bao gồm chi phí nhiên liệu, nhân công lái xe, cầu đường, bến bãi được tập hợp trên TK 154 và kết chuyển sang TK 632 khi xác định doanh thu.",
    })
    blocks.append({
        "type": "para",
        "text": "10. Nguyên tắc ghi nhận chi phí quản lý kinh doanh: Chi phí quản lý kinh doanh (TK 642) bao gồm chi phí nhân viên quản lý, bảo hiểm, khấu hao TSCĐ, phân bổ CCDC và chi phí dịch vụ mua ngoài phục vụ bộ phận quản lý.",
    })

    tax_policy = "11. Thuế thu nhập doanh nghiệp: Thuế suất thuế TNDN áp dụng theo quy định hiện hành."
    if is_cur.get("51", 0) == 0:
        tax_policy += " Trong kỳ chưa ghi nhận chi phí thuế TNDN."
    blocks.append({
        "type": "para",
        "text": tax_policy,
    })

    # V. THÔNG TIN BỔ SUNG CHO CÁC KHOẢN MỤC TRÌNH BÀY TRONG BÁO CÁO TÌNH HÌNH TÀI CHÍNH
    blocks.append({
        "type": "heading",
        "text": "V. THÔNG TIN BỔ SUNG CHO CÁC KHOẢN MỤC TRÌNH BÀY TRONG BÁO CÁO TÌNH HÌNH TÀI CHÍNH",
    })

    def get_leaves(prefix: str | tuple[str, ...]) -> list[dict]:
        if prefix in ("131", "331", "334"):
            # TK 131, 331, 334 chỉ trình bày ở cấp 1
            return [r for r in (tb_cur.get("all_rows") or tb_cur.get("rows") or [])
                    if r.get("is_parent") and str(r.get("code") or "").strip() == prefix]
        res = []
        for r in cur_leaf_rows:
            c = str(r.get("code") or "").strip()
            if (isinstance(prefix, str) and c.startswith(prefix)) or (isinstance(prefix, tuple) and c.startswith(prefix)):
                res.append(r)
        return res

    net_cur_fn = _net_map(cur_leaf_rows, "close")
    net_open_fn = _net_map(cur_leaf_rows, "open")

    # 1. Tiền
    tien_leaves = get_leaves(("111", "112", "113"))
    tien_rows: list[list[Any]] = []
    for r in tien_leaves:
        cd = int(round(float(r.get("close_dr", 0)) - float(r.get("close_cr", 0))))
        od = int(round(float(r.get("open_dr", 0)) - float(r.get("open_cr", 0))))
        if cd != 0 or od != 0:
            tien_rows.append([f"{r.get('code')} - {r.get('name')}", cd, od])
    tien_cur = bs_cur.get("110", 0)
    tien_open = bs_open.get("110", 0)
    if not tien_rows and tien_cur == 0 and tien_open == 0:
        blocks.append({"type": "para", "text": "1. Tiền: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "1. Tiền"})
        tien_rows.append(["Cộng", tien_cur, tien_open])
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Số cuối năm", "Số đầu năm"],
            "rows": tien_rows,
            "bold_rows": [len(tien_rows) - 1],
            "num_from": 1,
        })

    # 2. Phải thu của khách hàng
    rec_leaves = get_leaves("131")
    rec_rows: list[list[Any]] = []
    for r in rec_leaves:
        cd = max(0, int(round(float(r.get("close_dr", 0)) - float(r.get("close_cr", 0)))))
        od = max(0, int(round(float(r.get("open_dr", 0)) - float(r.get("open_cr", 0)))))
        if cd != 0 or od != 0:
            rec_rows.append([f"{r.get('code')} - {r.get('name')}", cd, od])
    rec_cur = bs_cur.get("131", 0)
    rec_open = bs_open.get("131", 0)
    if not rec_rows and rec_cur == 0 and rec_open == 0:
        blocks.append({"type": "para", "text": "2. Phải thu của khách hàng: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "2. Phải thu của khách hàng"})
        rec_rows.append(["Cộng", rec_cur, rec_open])
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Số cuối năm", "Số đầu năm"],
            "rows": rec_rows,
            "bold_rows": [len(rec_rows) - 1],
            "num_from": 1,
        })

    # 3. Phải thu ngắn hạn khác
    oth_rec_rows: list[list[Any]] = []
    c1_cur = net_cur_fn("136") + (net_cur_fn("138") - net_cur_fn("1381"))
    c1_open = net_open_fn("136") + (net_open_fn("138") - net_open_fn("1381"))
    if c1_cur != 0 or c1_open != 0:
        oth_rec_rows.append(["Phải thu khác (TK 136, 138 trừ 1381)", c1_cur, c1_open])

    c2_cur = net_cur_fn("141")
    c2_open = net_open_fn("141")
    if c2_cur != 0 or c2_open != 0:
        oth_rec_rows.append(["Tạm ứng (TK 141)", c2_cur, c2_open])

    c3_cur = max(0, net_cur_fn("338"))
    c3_open = max(0, net_open_fn("338"))
    if c3_cur != 0 or c3_open != 0:
        oth_rec_rows.append(["Phải trả khác có số dư Nợ (TK 338)", c3_cur, c3_open])

    c4_cur = max(0, net_cur_fn("334"))
    c4_open = max(0, net_open_fn("334"))
    if c4_cur != 0 or c4_open != 0:
        oth_rec_rows.append(["Phải trả người lao động có số dư Nợ (TK 334)", c4_cur, c4_open])

    row_133_cur = bs_cur.get("133", 0)
    row_133_open = bs_open.get("133", 0)
    if not oth_rec_rows and row_133_cur == 0 and row_133_open == 0:
        blocks.append({"type": "para", "text": "3. Phải thu ngắn hạn khác: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "3. Phải thu ngắn hạn khác"})
        oth_rec_rows.append(["Cộng", row_133_cur, row_133_open])
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Số cuối năm", "Số đầu năm"],
            "rows": oth_rec_rows,
            "bold_rows": [len(oth_rec_rows) - 1],
            "num_from": 1,
        })

    # 4. Hàng tồn kho
    inv_leaves = get_leaves(("151", "152", "153", "154", "155", "156", "157", "158"))
    inv_rows: list[list[Any]] = []
    for r in inv_leaves:
        cd = int(round(float(r.get("close_dr", 0)) - float(r.get("close_cr", 0))))
        od = int(round(float(r.get("open_dr", 0)) - float(r.get("open_cr", 0))))
        if cd != 0 or od != 0:
            inv_rows.append([f"{r.get('code')} - {r.get('name')}", cd, od])
    inv_cur = bs_cur.get("141", 0)
    inv_open = bs_open.get("141", 0)
    if not inv_rows and inv_cur == 0 and inv_open == 0:
        blocks.append({"type": "para", "text": "4. Hàng tồn kho: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "4. Hàng tồn kho"})
        inv_rows.append(["Cộng", inv_cur, inv_open])
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Số cuối năm", "Số đầu năm"],
            "rows": inv_rows,
            "bold_rows": [len(inv_rows) - 1],
            "num_from": 1,
        })

    # 5. Tài sản cố định hữu hình
    tsc_cols = [
        "Khoản mục",
        "Nhà cửa, vật kiến trúc",
        "Máy móc, thiết bị",
        "Phương tiện vận tải, truyền dẫn",
        "Thiết bị, dụng cụ quản lý",
        "TSCĐ khác",
        "Cộng",
    ]
    categories = [
        "Nhà cửa, vật kiến trúc",
        "Máy móc, thiết bị",
        "Phương tiện vận tải, truyền dẫn",
        "Thiết bị, dụng cụ quản lý",
        "TSCĐ khác",
    ]
    if not dep_rows:
        blocks.append({"type": "para", "text": "5. Tài sản cố định hữu hình: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "5. Tài sản cố định hữu hình"})
        c_open = [0] * 5
        c_inc = [0] * 5
        c_dec = [0] * 5
        d_open = [0] * 5
        d_inc = [0] * 5
        d_close = [0] * 5

        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)

        for r in dep_rows:
            at = (r.get("asset_type") or "").strip()
            cat_idx = categories.index(at) if at in categories[:-1] else 4

            orig = int(round(float(r.get("original_price") or 0)))
            cum = int(round(float(r.get("cumulative_depreciation") or 0)))
            yr_dep = int(round(float(r.get("year_depreciation_total") or 0)))
            pr_dep = cum - yr_dep

            inc_date = r.get("increase_date")
            if isinstance(inc_date, str):
                try:
                    inc_date = date.fromisoformat(inc_date)
                except Exception:
                    inc_date = None
            dec_date = r.get("decreased_on")
            if isinstance(dec_date, str):
                try:
                    dec_date = date.fromisoformat(dec_date)
                except Exception:
                    dec_date = None

            if inc_date and inc_date < year_start:
                c_open[cat_idx] += orig
            elif inc_date and year_start <= inc_date <= year_end:
                c_inc[cat_idx] += orig

            if dec_date and year_start <= dec_date <= year_end:
                c_dec[cat_idx] -= orig  # ghi số âm

            if pr_dep != 0:
                d_open[cat_idx] -= abs(pr_dep)
            if yr_dep != 0:
                d_inc[cat_idx] -= abs(yr_dep)
            if cum != 0:
                d_close[cat_idx] -= abs(cum)

        c_close = [c_open[i] + c_inc[i] + c_dec[i] for i in range(5)]
        r_open = [c_open[i] + d_open[i] for i in range(5)]
        r_close = [c_close[i] + d_close[i] for i in range(5)]

        t_rows = [
            ["I. Nguyên giá", "", "", "", "", "", ""],
            ["- Số dư đầu năm", *c_open, sum(c_open)],
            ["- Mua sắm trong năm", *c_inc, sum(c_inc)],
            ["- Thanh lý, nhượng bán", *c_dec, sum(c_dec)],
            ["- Số dư cuối năm", *c_close, sum(c_close)],
            ["II. Giá trị hao mòn lũy kế", "", "", "", "", "", ""],
            ["- Số dư đầu năm", *d_open, sum(d_open)],
            ["- Khấu hao trong năm", *d_inc, sum(d_inc)],
            ["- Số dư cuối năm", *d_close, sum(d_close)],
            ["III. Giá trị còn lại", "", "", "", "", "", ""],
            ["- Số dư đầu năm", *r_open, sum(r_open)],
            ["- Số dư cuối năm", *r_close, sum(r_close)],
        ]
        blocks.append({
            "type": "table",
            "columns": tsc_cols,
            "rows": t_rows,
            "bold_rows": [0, 4, 5, 8, 9, 10, 11],
            "num_from": 1,
        })

    # 6. Chi phí trả trước
    pre_leaves = get_leaves("242")
    pre_rows: list[list[Any]] = []
    for r in pre_leaves:
        cd = int(round(float(r.get("close_dr", 0)) - float(r.get("close_cr", 0))))
        od = int(round(float(r.get("open_dr", 0)) - float(r.get("open_cr", 0))))
        if cd != 0 or od != 0:
            pre_rows.append([f"{r.get('code')} - {r.get('name')}", cd, od])
    tot_242_cur = net_cur_fn("242")
    tot_242_open = net_open_fn("242")
    short_cur = short_242 if short_242 is not None else tot_242_cur
    long_cur = tot_242_cur - short_cur
    short_open = tot_242_open
    long_open = 0

    if not pre_rows and tot_242_cur == 0 and tot_242_open == 0:
        blocks.append({"type": "para", "text": "6. Chi phí trả trước: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "6. Chi phí trả trước"})
        pre_rows.append(["- Chi phí trả trước ngắn hạn", short_cur, short_open])
        pre_rows.append(["- Chi phí trả trước dài hạn", long_cur, long_open])
        pre_rows.append(["Cộng", tot_242_cur, tot_242_open])
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Số cuối năm", "Số đầu năm"],
            "rows": pre_rows,
            "bold_rows": [len(pre_rows) - 1],
            "num_from": 1,
        })

    # 7. Phải trả người bán ngắn hạn
    pay_leaves = get_leaves("331")
    pay_rows: list[list[Any]] = []
    for r in pay_leaves:
        cd = max(0, int(round(float(r.get("close_cr", 0)) - float(r.get("close_dr", 0)))))
        od = max(0, int(round(float(r.get("open_cr", 0)) - float(r.get("open_dr", 0)))))
        if cd != 0 or od != 0:
            pay_rows.append([f"{r.get('code')} - {r.get('name')}", cd, od])
    pay_cur = bs_cur.get("411", 0)
    pay_open = bs_open.get("411", 0)
    if not pay_rows and pay_cur == 0 and pay_open == 0:
        blocks.append({"type": "para", "text": "7. Phải trả người bán ngắn hạn: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "7. Phải trả người bán ngắn hạn"})
        pay_rows.append(["Cộng", pay_cur, pay_open])
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Số cuối năm", "Số đầu năm"],
            "rows": pay_rows,
            "bold_rows": [len(pay_rows) - 1],
            "num_from": 1,
        })

    # 8. Thuế và các khoản phải nộp Nhà nước
    tax_leaves = get_leaves("333")
    tax_rows: list[list[Any]] = []
    for r in tax_leaves:
        od = int(round(float(r.get("open_cr", 0)) - float(r.get("open_dr", 0))))
        p_cr = int(round(float(r.get("ps_cr", 0))))
        p_dr = int(round(float(r.get("ps_dr", 0))))
        cd = int(round(float(r.get("close_cr", 0)) - float(r.get("close_dr", 0))))
        if od != 0 or p_cr != 0 or p_dr != 0 or cd != 0:
            tax_rows.append([f"{r.get('code')} - {r.get('name')}", od, p_cr, p_dr, cd])
    if not tax_rows:
        blocks.append({"type": "para", "text": "8. Thuế và các khoản phải nộp Nhà nước: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "8. Thuế và các khoản phải nộp Nhà nước"})
        tot_od = sum(r[1] for r in tax_rows)
        tot_p_cr = sum(r[2] for r in tax_rows)
        tot_p_dr = sum(r[3] for r in tax_rows)
        tot_cd = sum(r[4] for r in tax_rows)
        tax_rows.append(["Cộng", tot_od, tot_p_cr, tot_p_dr, tot_cd])
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Số đầu năm", "Số phải nộp trong năm", "Số đã nộp trong năm", "Số cuối năm"],
            "rows": tax_rows,
            "bold_rows": [len(tax_rows) - 1],
            "num_from": 1,
        })

    # 9. Phải trả người lao động
    sal_leaves = get_leaves("334")
    sal_rows: list[list[Any]] = []
    for r in sal_leaves:
        cd = max(0, int(round(float(r.get("close_cr", 0)) - float(r.get("close_dr", 0)))))
        od = max(0, int(round(float(r.get("open_cr", 0)) - float(r.get("open_dr", 0)))))
        if cd != 0 or od != 0:
            sal_rows.append([f"{r.get('code')} - {r.get('name')}", cd, od])
    sal_cur = bs_cur.get("414", 0)
    sal_open = bs_open.get("414", 0)
    if not sal_rows and sal_cur == 0 and sal_open == 0:
        blocks.append({"type": "para", "text": "9. Phải trả người lao động: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "9. Phải trả người lao động"})
        sal_rows.append(["Cộng", sal_cur, sal_open])
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Số cuối năm", "Số đầu năm"],
            "rows": sal_rows,
            "bold_rows": [len(sal_rows) - 1],
            "num_from": 1,
        })

    # 10. Phải trả ngắn hạn khác
    oth_pay_leaves = get_leaves(("335", "338"))
    oth_pay_rows: list[list[Any]] = []
    for r in oth_pay_leaves:
        cd = max(0, int(round(float(r.get("close_cr", 0)) - float(r.get("close_dr", 0)))))
        od = max(0, int(round(float(r.get("open_cr", 0)) - float(r.get("open_dr", 0)))))
        if cd != 0 or od != 0:
            oth_pay_rows.append([f"{r.get('code')} - {r.get('name')}", cd, od])
    oth_pay_cur = bs_cur.get("415", 0)
    oth_pay_open = bs_open.get("415", 0)
    if not oth_pay_rows and oth_pay_cur == 0 and oth_pay_open == 0:
        blocks.append({"type": "para", "text": "10. Phải trả ngắn hạn khác: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "10. Phải trả ngắn hạn khác"})
        oth_pay_rows.append(["Cộng", oth_pay_cur, oth_pay_open])
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Số cuối năm", "Số đầu năm"],
            "rows": oth_pay_rows,
            "bold_rows": [len(oth_pay_rows) - 1],
            "num_from": 1,
        })

    # 11. Vay và nợ thuê tài chính
    debt_leaves = get_leaves(("341", "343"))
    debt_rows: list[list[Any]] = []
    for r in debt_leaves:
        cd = max(0, int(round(float(r.get("close_cr", 0)) - float(r.get("close_dr", 0)))))
        od = max(0, int(round(float(r.get("open_cr", 0)) - float(r.get("open_dr", 0)))))
        if cd != 0 or od != 0:
            debt_rows.append([f"{r.get('code')} - {r.get('name')}", cd, od])
    debt_cur = bs_cur.get("416", 0)
    debt_open = bs_open.get("416", 0)
    if not debt_rows and debt_cur == 0 and debt_open == 0:
        blocks.append({"type": "para", "text": "11. Vay và nợ thuê tài chính: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "11. Vay và nợ thuê tài chính"})
        debt_rows.append(["Cộng", debt_cur, debt_open])
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Số cuối năm", "Số đầu năm"],
            "rows": debt_rows,
            "bold_rows": [len(debt_rows) - 1],
            "num_from": 1,
        })

    # 12. Vốn chủ sở hữu
    blocks.append({"type": "subheading", "text": "12. Vốn chủ sở hữu"})
    cap_leaves = get_leaves("411")
    blocks.append({"type": "para", "text": "a) Chi tiết vốn góp của chủ sở hữu:"})
    cap_rows: list[list[Any]] = []
    tot_cap = 0
    for r in cap_leaves:
        val = max(0, int(round(float(r.get("close_cr", 0)) - float(r.get("close_dr", 0)))))
        tot_cap += val
    for r in cap_leaves:
        val = max(0, int(round(float(r.get("close_cr", 0)) - float(r.get("close_dr", 0)))))
        if val > 0:
            ratio = (val / tot_cap * 100.0) if tot_cap else 0.0
            ratio_str = f"{ratio:.2f}".replace(".", ",")
            owner_name = r.get("name", "").replace("Vốn góp của ", "").strip()
            cap_rows.append([owner_name, val, ratio_str])
    cap_rows.append(["Cộng", tot_cap, "100,00"])
    blocks.append({
        "type": "table",
        "columns": ["Chủ sở hữu / Thành viên", "Số vốn góp", "Tỷ lệ (%)"],
        "rows": cap_rows,
        "bold_rows": [len(cap_rows) - 1],
        "num_from": 1,
    })

    blocks.append({"type": "para", "text": "b) Bảng biến động vốn chủ sở hữu:"})
    vg_open = bs_open.get("511", 0)
    ln_open = bs_open.get("517", 0)
    vg_inc = sum(int(round(float(r.get("ps_cr", 0)))) for r in cap_leaves)
    ln_inc = is_cur.get("60", 0)
    vg_dec = sum(int(round(float(r.get("ps_dr", 0)))) for r in cap_leaves)
    ln_dec = sum(int(round(float(r.get("ps_dr", 0)))) for r in get_leaves("421"))
    vg_close = bs_cur.get("511", 0)
    ln_close = bs_cur.get("517", 0)

    mv_rows = [
        ["Số dư đầu năm", vg_open, ln_open, vg_open + ln_open],
        ["Tăng trong năm", vg_inc, ln_inc, vg_inc + ln_inc],
        ["Giảm trong năm", vg_dec, ln_dec, vg_dec + ln_dec],
        ["Số dư cuối năm", vg_close, ln_close, vg_close + ln_close],
    ]
    blocks.append({
        "type": "table",
        "columns": ["Khoản mục", "Vốn góp của chủ sở hữu", "Lợi nhuận sau thuế chưa phân phối", "Cộng"],
        "rows": mv_rows,
        "bold_rows": [0, 3],
        "num_from": 1,
    })

    # VI. THÔNG TIN BỔ SUNG CHO CÁC KHOẢN MỤC TRÌNH BÀY TRONG BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH
    blocks.append({
        "type": "heading",
        "text": "VI. THÔNG TIN BỔ SUNG CHO CÁC KHOẢN MỤC TRÌNH BÀY TRONG BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH",
    })

    prev_leaf_rows = [r for r in (tb_prev.get("all_rows") or tb_prev.get("rows") or []) if not r.get("is_parent")]

    def add_pnl_subtable(title: str, prefix: str | tuple[str, ...], code: str, is_revenue: bool):
        c_leaves = [r for r in cur_leaf_rows if str(r.get("code") or "").startswith(prefix)]
        p_leaves = [r for r in prev_leaf_rows if str(r.get("code") or "").startswith(prefix)]

        cur_map: dict[str, int] = {}
        for r in c_leaves:
            amt = int(round(float(r.get("ps_cr" if is_revenue else "ps_dr", 0))))
            if amt != 0:
                nm = (r.get("name") or "").strip()
                cur_map[nm] = cur_map.get(nm, 0) + amt

        prev_map: dict[str, int] = {}
        for r in p_leaves:
            amt = int(round(float(r.get("ps_cr" if is_revenue else "ps_dr", 0))))
            if amt != 0:
                nm = (r.get("name") or "").strip()
                prev_map[nm] = prev_map.get(nm, 0) + amt

        all_names = sorted(list(set(cur_map.keys()) | set(prev_map.keys())))
        sub_rows: list[list[Any]] = []
        for nm in all_names:
            c_v = cur_map.get(nm, 0)
            p_v = prev_map.get(nm, 0)
            if c_v != 0 or p_v != 0:
                sub_rows.append([nm, c_v, p_v])

        tot_c = is_cur.get(code, 0)
        tot_p = is_prev.get(code, 0)

        if not sub_rows and tot_c == 0 and tot_p == 0:
            blocks.append({"type": "para", "text": f"{title}: Không phát sinh."})
        else:
            blocks.append({"type": "subheading", "text": title})
            sub_rows.append(["Cộng", tot_c, tot_p])
            blocks.append({
                "type": "table",
                "columns": ["Khoản mục", "Năm nay", "Năm trước"],
                "rows": sub_rows,
                "bold_rows": [len(sub_rows) - 1],
                "num_from": 1,
            })

    add_pnl_subtable("1. Doanh thu bán hàng và cung cấp dịch vụ", "511", "01", is_revenue=True)

    c02 = is_cur.get("02", 0)
    p02 = is_prev.get("02", 0)
    if c02 == 0 and p02 == 0:
        blocks.append({"type": "para", "text": "2. Các khoản giảm trừ doanh thu: Không phát sinh."})
    else:
        blocks.append({"type": "subheading", "text": "2. Các khoản giảm trừ doanh thu"})
        blocks.append({
            "type": "table",
            "columns": ["Khoản mục", "Năm nay", "Năm trước"],
            "rows": [["Các khoản giảm trừ", c02, p02], ["Cộng", c02, p02]],
            "bold_rows": [1],
            "num_from": 1,
        })

    add_pnl_subtable("3. Giá vốn hàng bán", "632", "11", is_revenue=False)
    add_pnl_subtable("4. Doanh thu hoạt động tài chính", "515", "21", is_revenue=True)
    add_pnl_subtable("5. Chi phí tài chính", "635", "22", is_revenue=False)
    add_pnl_subtable("6. Chi phí quản lý kinh doanh", "642", "24", is_revenue=False)

    # 7. Thu nhập khác và Chi phí khác
    blocks.append({"type": "subheading", "text": "7. Thu nhập khác và Chi phí khác"})
    add_pnl_subtable("a) Thu nhập khác", "711", "31", is_revenue=True)
    add_pnl_subtable("b) Chi phí khác", "811", "32", is_revenue=False)

    # 8. Chi phí thuế TNDN
    add_pnl_subtable("8. Chi phí thuế thu nhập doanh nghiệp hiện hành", "821", "51", is_revenue=False)

    # VII. NHỮNG THÔNG TIN KHÁC
    blocks.append({
        "type": "heading",
        "text": "VII. NHỮNG THÔNG TIN KHÁC",
    })
    blocks.append({
        "type": "para",
        "text": "1. Nợ tiềm tàng, cam kết và các thông tin tài chính khác: Không có.",
    })
    blocks.append({
        "type": "para",
        "text": "2. Sự kiện phát sinh sau ngày kết thúc kỳ kế toán: Không có.",
    })

    owner_list = []
    for r in cap_leaves:
        val = max(0, int(round(float(r.get("close_cr", 0)) - float(r.get("close_dr", 0)))))
        if val > 0:
            nm = r.get("name", "").replace("Vốn góp của ", "").strip()
            owner_list.append(f"{nm} ({_format_number_vn(val)} đồng)")
    if owner_list:
        rel_text = "Các thành viên góp vốn: " + ", ".join(owner_list) + "."
    else:
        rel_text = "Không có."
    blocks.append({
        "type": "para",
        "text": f"3. Thông tin về các bên liên quan: {rel_text}",
    })

    blocks.append({
        "type": "para",
        "text": "4. Thông tin về hoạt động liên tục: Công ty hoạt động liên tục.",
    })

    return blocks


def get_financial_notes_data(
    year: int,
    date_from: date,
    date_to: date,
    company_id: int | None = None,
) -> dict[str, Any]:
    """Lấy dữ liệu Bản thuyết minh BCTC (Mẫu B09-DNN)."""
    cid = company_id or get_current_company_id() or 33
    res = db._execute_query(
        "SELECT id, name, address, tax_code, director_name, business_activity FROM public.invoice_companies WHERE id = %s",
        (cid,), fetch=True, commit=False,
    )
    company: dict[str, Any] = dict(res) if res else {}

    tb_cur = get_trial_balance_data(company_id=cid, year=year, date_from=date_from, date_to=date_to)
    tb_prev = get_trial_balance_data(company_id=cid, year=year - 1,
                                     date_from=_shift_year_back(date_from), date_to=_shift_year_back(date_to))

    net_close = _net_map(tb_cur.get("all_rows") or tb_cur.get("rows") or [], "close")
    short_242 = _short_term_242(cid, year, date_to, net_242=net_close("242"))

    bs_cur = build_balance_sheet_values(tb_cur, "close", short_242)
    bs_open = build_balance_sheet_values(tb_cur, "open", None)

    is_cur = build_income_statement(tb_cur)
    is_prev = build_income_statement(tb_prev) if tb_prev else {k: 0 for k in is_cur}

    dep_rows = get_depreciation_data(company_id=cid, year=year).get("rows") or []

    # Lao động = người có hồ sơ lương của công ty (không tính tài khoản quản trị hệ thống)
    staff_row = db._execute_query(
        """
        SELECT COUNT(*) AS n
          FROM public.staff_salary_profiles p
          JOIN public.users u ON u.id = p.user_id
         WHERE %s = ANY(u.company_ids)
        """,
        (cid,), fetch=True, commit=False,
    )
    staff_count = int(staff_row["n"] or 0) if staff_row else None

    company_name = company.get("name") or tb_cur.get("company_name") or "CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH"
    address = company.get("address") or tb_cur.get("address") or ""
    tax_code = company.get("tax_code") or tb_cur.get("tax_code") or ""
    director_name = company.get("director_name") or tb_cur.get("director_name") or "Chủ sở hữu"
    business_activity = company.get("business_activity") or "Vận tải hàng hóa bằng đường bộ"

    ctx = {
        "company": {
            "name": company_name,
            "address": address,
            "tax_code": tax_code,
            "director_name": director_name,
            "business_activity": business_activity,
        },
        "year": year,
        "date_from": date_from,
        "date_to": date_to,
        "tb_cur": tb_cur,
        "tb_prev": tb_prev,
        "bs_cur": bs_cur,
        "bs_open": bs_open,
        "is_cur": is_cur,
        "is_prev": is_prev,
        "dep_rows": dep_rows,
        "staff_count": staff_count,
        "short_242": short_242,
    }

    blocks = build_notes_blocks(ctx)

    return {
        "company_name": company_name,
        "address": address,
        "tax_code": tax_code,
        "director_name": director_name,
        "business_activity": business_activity,
        "year": year,
        "date_from": date_from,
        "date_to": date_to,
        "blocks": blocks,
    }


def export_financial_notes_excel(
    year: int,
    date_from: date,
    date_to: date,
    company_id: int | None = None,
) -> bytes:
    """Xuất Bản thuyết minh Báo cáo tài chính (Mẫu B09-DNN) ra file Excel."""
    data = get_financial_notes_data(year=year, date_from=date_from, date_to=date_to, company_id=company_id)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "B09-DNN"
    ws.views.sheetView[0].showGridLines = True

    col_widths = {
        "A": 46.0,
        "B": 18.0,
        "C": 18.0,
        "D": 18.0,
        "E": 18.0,
        "F": 18.0,
        "G": 18.0,
        "H": 18.0,
    }
    for col, width in col_widths.items():
        ws.column_dimensions[col].width = width

    font_company = Font(name="Times New Roman", size=11, bold=True)
    font_addr = Font(name="Times New Roman", size=10, italic=True)
    font_mau = Font(name="Times New Roman", size=11, bold=True)
    font_tt = Font(name="Times New Roman", size=9, italic=True)
    font_title = Font(name="Times New Roman", size=14, bold=True)
    font_heading = Font(name="Times New Roman", size=11, bold=True)
    Font(name="Times New Roman", size=10, bold=True)
    font_para = Font(name="Times New Roman", size=10)
    font_tbl_hdr = Font(name="Times New Roman", size=10, bold=True)
    font_sign_title = Font(name="Times New Roman", size=11, bold=True)
    font_sign_sub = Font(name="Times New Roman", size=10, italic=True)

    border_thin = Border(
        left=Side(style="thin", color="A0A0A0"),
        right=Side(style="thin", color="A0A0A0"),
        top=Side(style="thin", color="A0A0A0"),
        bottom=Side(style="thin", color="A0A0A0"),
    )
    fill_hdr = PatternFill(start_color="F2F4F7", end_color="F2F4F7", fill_type="solid")

    ws["A1"] = data.get("company_name", "")
    ws["A1"].font = font_company
    ws["A2"] = data.get("address", "")
    ws["A2"].font = font_addr
    ws["A3"] = f"Mã số thuế: {data.get('tax_code', '')}"
    ws["A3"].font = font_addr

    ws.merge_cells("F1:H1")
    ws["F1"] = "Mẫu số B09 - DNN"
    ws["F1"].font = font_mau
    ws["F1"].alignment = Alignment(horizontal="right")

    ws.merge_cells("F2:H2")
    ws["F2"] = "(Ban hành theo Thông tư số 133/2016/TT-BTC"
    ws["F2"].font = font_tt
    ws["F2"].alignment = Alignment(horizontal="right")

    ws.merge_cells("F3:H3")
    ws["F3"] = "ngày 26/8/2016 của Bộ Tài chính)"
    ws["F3"].font = font_tt
    ws["F3"].alignment = Alignment(horizontal="right")

    row_idx = 5
    for b in data.get("blocks", []):
        b_type = b.get("type")
        if b_type == "title":
            ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=8)
            c = ws.cell(row_idx, 1, b.get("text", ""))
            c.font = font_title
            c.alignment = Alignment(horizontal="center", vertical="center")
            row_idx += 1
        elif b_type == "heading":
            row_idx += 1
            ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=8)
            c = ws.cell(row_idx, 1, b.get("text", ""))
            c.font = font_heading
            c.alignment = Alignment(horizontal="left", vertical="center")
            row_idx += 1
        elif b_type == "subheading":
            ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=8)
            c = ws.cell(row_idx, 1, b.get("text", ""))
            is_it = b.get("italic", False)
            c.font = Font(name="Times New Roman", size=10, bold=True, italic=is_it)
            align = b.get("align", "left")
            c.alignment = Alignment(horizontal=align, vertical="center")
            row_idx += 1
        elif b_type == "para":
            ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=8)
            text = str(b.get("text") or "")
            c = ws.cell(row_idx, 1, text)
            c.font = font_para
            align = b.get("align", "left")
            c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
            lines = text.count("\n") + max(1, len(text) // 90)
            ws.row_dimensions[row_idx].height = max(18.0, 15.0 * lines)
            row_idx += 1
        elif b_type == "table":
            cols = b.get("columns", [])
            rows = b.get("rows", [])
            bold_rows = set(b.get("bold_rows", []))
            num_from = b.get("num_from", 1)

            for col_i, col_name in enumerate(cols, 1):
                cell = ws.cell(row_idx, col_i, col_name)
                cell.font = font_tbl_hdr
                cell.fill = fill_hdr
                cell.border = border_thin
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            row_idx += 1

            for r_i, r_data in enumerate(rows):
                is_bold = r_i in bold_rows
                f_row = Font(name="Times New Roman", size=10, bold=is_bold)
                for col_i, val in enumerate(r_data, 1):
                    cell = ws.cell(row_idx, col_i)
                    cell.font = f_row
                    cell.border = border_thin
                    if col_i - 1 < num_from:
                        cell.value = str(val) if val is not None else ""
                        cell.alignment = Alignment(horizontal="left", vertical="center")
                    else:
                        if isinstance(val, (int, float)):
                            cell.value = val
                            cell.number_format = '#,##0;(#,##0);"-"'
                            cell.alignment = Alignment(horizontal="right", vertical="center")
                        else:
                            cell.value = str(val) if val is not None else ""
                            cell.alignment = Alignment(horizontal="right", vertical="center")
                row_idx += 1
            row_idx += 1

    # Khối chữ ký cuối
    row_idx += 1
    ws.merge_cells(start_row=row_idx, start_column=6, end_row=row_idx, end_column=8)
    cell_date = ws.cell(row_idx, 6, f"Ngày {date_to.day:02d} tháng {date_to.month:02d} năm {date_to.year}")
    cell_date.font = font_sign_sub
    cell_date.alignment = Alignment(horizontal="center")

    title_row = row_idx + 1
    ws.merge_cells(start_row=title_row, start_column=1, end_row=title_row, end_column=2)
    ws.cell(title_row, 1, "Người lập biểu").font = font_sign_title
    ws.cell(title_row, 1).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=title_row, start_column=3, end_row=title_row, end_column=5)
    ws.cell(title_row, 3, "Kế toán trưởng").font = font_sign_title
    ws.cell(title_row, 3).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=title_row, start_column=6, end_row=title_row, end_column=8)
    ws.cell(title_row, 6, "Giám đốc").font = font_sign_title
    ws.cell(title_row, 6).alignment = Alignment(horizontal="center")

    sub_row = title_row + 1
    ws.merge_cells(start_row=sub_row, start_column=1, end_row=sub_row, end_column=2)
    ws.cell(sub_row, 1, "(Ký, họ tên)").font = font_sign_sub
    ws.cell(sub_row, 1).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=sub_row, start_column=3, end_row=sub_row, end_column=5)
    ws.cell(sub_row, 3, "(Ký, họ tên)").font = font_sign_sub
    ws.cell(sub_row, 3).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=sub_row, start_column=6, end_row=sub_row, end_column=8)
    ws.cell(sub_row, 6, "(Ký, họ tên, đóng dấu)").font = font_sign_sub
    ws.cell(sub_row, 6).alignment = Alignment(horizontal="center")

    dir_row = sub_row + 4
    ws.merge_cells(start_row=dir_row, start_column=6, end_row=dir_row, end_column=8)
    ws.cell(dir_row, 6, data.get("director_name", "")).font = font_company
    ws.cell(dir_row, 6).alignment = Alignment(horizontal="center")

    out = BytesIO()
    wb.save(out)
    return out.getvalue()
