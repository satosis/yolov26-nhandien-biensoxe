"""Cầu nối HTTP của frontend.modules.prospects.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.prospects.controller as controller


def list_page(
    request: Request,
    status: str = Query(''),
    q: str = Query(''),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('prospects')),
):
    'Trang danh sách khách tiềm năng (chỉ đọc DB).'
    return controller.list_page(request=request, status=status, q=q, page=page, user=user)


def create_prospect(
    name: str = Form(''),
    company: str = Form(''),
    phone: str = Form(''),
    zalo: str = Form(''),
    segment: str = Form('other'),
    province: str = Form(''),
    source: str = Form('manual'),
    status: str = Form('new'),
    next_contact: str = Form(''),
    note: str = Form(''),
    filter_status: str = Query(''),
    filter_q: str = Query(''),
    user: dict = Depends(require_menu('prospects')),
):
    'Thêm mới một khách tiềm năng.'
    return controller.create_prospect(name=name, company=company, phone=phone, zalo=zalo, segment=segment, province=province, source=source, status=status, next_contact=next_contact, note=note, filter_status=filter_status, filter_q=filter_q, user=user)


def import_prospects(
    text: str = Form(''),
    segment: str = Form('other'),
    province: str = Form(''),
    filter_status: str = Query(''),
    filter_q: str = Query(''),
    user: dict = Depends(require_menu('prospects')),
):
    'Nhập danh sách khách từ văn bản phân tách cột.'
    return controller.import_prospects(text=text, segment=segment, province=province, filter_status=filter_status, filter_q=filter_q, user=user)


def update_prospect(
    prospect_id: int,
    name: str = Form(''),
    company: str = Form(''),
    phone: str = Form(''),
    zalo: str = Form(''),
    segment: str = Form('other'),
    province: str = Form(''),
    source: str = Form('manual'),
    status: str = Form('new'),
    next_contact: str = Form(''),
    note: str = Form(''),
    filter_status: str = Query(''),
    filter_q: str = Query(''),
    user: dict = Depends(require_menu('prospects')),
):
    'Cập nhật thông tin khách tiềm năng.'
    return controller.update_prospect(prospect_id=prospect_id, name=name, company=company, phone=phone, zalo=zalo, segment=segment, province=province, source=source, status=status, next_contact=next_contact, note=note, filter_status=filter_status, filter_q=filter_q, user=user)


def delete_prospect(
    prospect_id: int,
    filter_status: str = Query(''),
    filter_q: str = Query(''),
    user: dict = Depends(require_menu('prospects')),
):
    'Xóa một khách tiềm năng.'
    return controller.delete_prospect(prospect_id=prospect_id, filter_status=filter_status, filter_q=filter_q, user=user)


def set_prospect_status(
    prospect_id: int,
    status: str = Form(...),
    next_contact: str = Form(''),
    filter_status: str = Query(''),
    filter_q: str = Query(''),
    user: dict = Depends(require_menu('prospects')),
):
    'Cập nhật nhanh trạng thái khách tiềm năng.'
    return controller.set_prospect_status(prospect_id=prospect_id, status=status, next_contact=next_contact, filter_status=filter_status, filter_q=filter_q, user=user)
