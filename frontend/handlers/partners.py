"""Cầu nối HTTP của frontend.modules.partners.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.partners.controller as controller


def list_page(
    request: Request,
    q: str = Query(''),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('partners')),
):
    return controller.list_page(request=request, q=q, page=page, user=user)


def create_partner(
    name: str = Form(''),
    phone: str = Form(''),
    kind: str = Form('other'),
    code: str = Form(''),
    commission_percent: str = Form('3.0'),
    active: str = Form(''),
    note: str = Form(''),
    user: dict = Depends(require_menu('partners')),
):
    return controller.create_partner(name=name, phone=phone, kind=kind, code=code, commission_percent=commission_percent, active=active, note=note, user=user)


def update_partner(
    partner_id: int,
    name: str = Form(''),
    phone: str = Form(''),
    kind: str = Form('other'),
    code: str = Form(''),
    commission_percent: str = Form('3.0'),
    active: str = Form(''),
    note: str = Form(''),
    user: dict = Depends(require_menu('partners')),
):
    return controller.update_partner(partner_id=partner_id, name=name, phone=phone, kind=kind, code=code, commission_percent=commission_percent, active=active, note=note, user=user)


def delete_partner(partner_id: int, user: dict = Depends(require_menu('partners'))):
    return controller.delete_partner(partner_id=partner_id, user=user)
