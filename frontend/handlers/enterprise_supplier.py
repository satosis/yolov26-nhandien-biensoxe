"""Cầu nối HTTP của frontend.modules.enterprise.supplier_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, UploadFile, File, Form
from frontend.deps import require_menu
from typing import Optional
import frontend.modules.enterprise.supplier_controller as controller


async def suppliers_page(
    request: Request,
    company_id: Optional[int] = Query(None),
    view: Optional[str] = Query(None),
    tab: Optional[str] = Query(None),
    book: Optional[str] = Query(None),
    supplier: Optional[str] = Query(None),
    product: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    debt_only: int = Query(0),
    page: int = Query(1),
    per_page: int = Query(100),
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.suppliers_page(request=request, company_id=company_id, view=view, tab=tab, book=book, supplier=supplier, product=product, month=month, date_from=date_from, date_to=date_to, q=q, debt_only=debt_only, page=page, per_page=per_page, user=user)


async def api_next_purchase_stt(
    request: Request,
    book: str = Query('bich'),
    company_id: Optional[int] = Query(None),
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.api_next_purchase_stt(request=request, book=book, company_id=company_id, user=user)


async def api_list_purchases(
    request: Request,
    company_id: Optional[int] = Query(None),
    book: str = Query('all'),
    supplier: Optional[str] = Query(None),
    product: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1),
    per_page: int = Query(100),
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.api_list_purchases(request=request, company_id=company_id, book=book, supplier=supplier, product=product, month=month, date_from=date_from, date_to=date_to, q=q, page=page, per_page=per_page, user=user)


async def api_create_purchase(request: Request, user: dict = Depends(require_menu('suppliers'))):
    return await controller.api_create_purchase(request=request, user=user)


async def api_get_purchase_order(
    request: Request,
    book_type: Optional[str] = Query(None),
    book: Optional[str] = Query(None),
    invoice_number: str = Query(''),
    company_id: Optional[int] = Query(None),
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.api_get_purchase_order(request=request, book_type=book_type, book=book, invoice_number=invoice_number, company_id=company_id, user=user)


async def api_save_purchase_order(request: Request, user: dict = Depends(require_menu('suppliers'))):
    return await controller.api_save_purchase_order(request=request, user=user)


async def api_update_purchase(
    purchase_id: int,
    request: Request,
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.api_update_purchase(purchase_id=purchase_id, request=request, user=user)


async def api_renumber_purchases(request: Request, user: dict = Depends(require_menu('suppliers'))):
    return await controller.api_renumber_purchases(request=request, user=user)


async def api_delete_purchase(
    purchase_id: int,
    request: Request,
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.api_delete_purchase(purchase_id=purchase_id, request=request, user=user)


async def api_list_debts(
    request: Request,
    company_id: Optional[int] = Query(None),
    book: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    debt_only: int = Query(0),
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.api_list_debts(request=request, company_id=company_id, book=book, month=month, q=q, debt_only=debt_only, user=user)


async def api_save_opening_debt(request: Request, user: dict = Depends(require_menu('suppliers'))):
    return await controller.api_save_opening_debt(request=request, user=user)


async def api_record_payment(request: Request, user: dict = Depends(require_menu('suppliers'))):
    return await controller.api_record_payment(request=request, user=user)


async def api_update_payment(
    payment_id: int,
    request: Request,
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.api_update_payment(payment_id=payment_id, request=request, user=user)


async def api_delete_payment(
    payment_id: int,
    request: Request,
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.api_delete_payment(payment_id=payment_id, request=request, user=user)


async def api_get_statement(
    supplier_name: str,
    request: Request,
    company_id: Optional[int] = Query(None),
    book_type: Optional[str] = Query(None),
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.api_get_statement(supplier_name=supplier_name, request=request, company_id=company_id, book_type=book_type, user=user)


async def api_import_excel(
    request: Request,
    file: UploadFile = File(...),
    book_type: str = Form('bich'),
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.api_import_excel(request=request, file=file, book_type=book_type, user=user)


async def export_purchases(
    request: Request,
    company_id: Optional[int] = Query(None),
    book: str = Query('all'),
    supplier: Optional[str] = Query(None),
    product: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    user: dict = Depends(require_menu('suppliers')),
):
    return await controller.export_purchases(request=request, company_id=company_id, book=book, supplier=supplier, product=product, month=month, q=q, user=user)
