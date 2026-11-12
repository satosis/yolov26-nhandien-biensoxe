"""Cầu nối HTTP của frontend.modules.transport.trips_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends, Query, Form, UploadFile, File
from frontend.deps import require_menu
from typing import Optional
from uuid import UUID
import frontend.modules.transport.trips_controller as controller


async def preview_bkav_cargo_invoices(
    request: Request,
    payload: controller.BkavSyncInput,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.preview_bkav_cargo_invoices(request=request, payload=payload, user=user)


async def import_bkav_cargo_invoices(
    request: Request,
    payload: controller.BkavSyncInput,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.import_bkav_cargo_invoices(request=request, payload=payload, user=user)


async def cargo_trips_page(
    request: Request,
    vehicle_id: str | None = Query(None),
    company_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    month: str | None = Query(None),
    status: str | None = Query(None),
    payment_status: str | None = Query(None),
    q: str | None = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.cargo_trips_page(request=request, vehicle_id=vehicle_id, company_id=company_id, page=page, month=month, status=status, payment_status=payment_status, q=q, user=user)


async def lookup_invoice_company(tax_code: str = Query(...), _: dict = Depends(require_menu('vehicles'))):
    return await controller.lookup_invoice_company(tax_code=tax_code, _=_)


async def refresh_invoice_company(company_id: int, _: dict = Depends(require_menu('vehicles'))):
    return await controller.refresh_invoice_company(company_id=company_id, _=_)


async def invoice_tax_suggestion(invoice_date: str = Query(...), _: dict = Depends(require_menu('vehicles'))):
    return await controller.invoice_tax_suggestion(invoice_date=invoice_date, _=_)


async def create_invoice_company(
    payload: controller.InvoiceCompanyInput,
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.create_invoice_company(payload=payload, _=_)


async def resolve_maps_url(url: str = Query(...), user: dict = Depends(require_menu('vehicles'))):
    return await controller.resolve_maps_url(url=url, user=user)


async def geocode_address(q: str = Query(...), user: dict = Depends(require_menu('vehicles'))):
    return await controller.geocode_address(q=q, user=user)


async def calculate_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.calculate_distance(lat1=lat1, lon1=lon1, lat2=lat2, lon2=lon2, user=user)


async def calculate_distance_by_address(
    origin: str = Query(...),
    destination: str = Query(...),
    waypoints: str = '[]',
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.calculate_distance_by_address(origin=origin, destination=destination, waypoints=waypoints, user=user)


async def normalize_invoice_locations(
    payload: controller.InvoiceLocationsInput,
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.normalize_invoice_locations(payload=payload, _=_)


async def google_maps_status_api(user: dict = Depends(require_menu('vehicles'))):
    return await controller.google_maps_status_api(user=user)


async def get_fuel_price_api(target_date: str = Query(None), user: dict = Depends(require_menu('vehicles'))):
    return await controller.get_fuel_price_api(target_date=target_date, user=user)


async def get_next_invoice_number_api(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.get_next_invoice_number_api(request=request, user=user)


async def add_cargo_trip_page(
    request: Request,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.add_cargo_trip_page(request=request, vehicle_id=vehicle_id, user=user)


async def create_cargo_trip(
    request: Request,
    vehicle_id: Optional[int] = None,
    invoice_date: str = Form(...),
    company_id: int = Form(...),
    service_name: str = Form(...),
    seller_company_id: Optional[int] = Form(None),
    origin: Optional[str] = Form(None),
    destination: Optional[str] = Form(None),
    fuel_liters: Optional[str] = Form(None),
    fuel_price_per_liter: Optional[str] = Form(None),
    freight_revenue: Optional[str] = Form(None),
    distance_km: Optional[str] = Form(None),
    vat_percent: Optional[str] = Form('8'),
    payment_method: Optional[str] = Form('CK'),
    payment_status: Optional[str] = Form('paid'),
    debt_amount: Optional[str] = Form(None),
    document_number: Optional[str] = Form(None),
    cargo_description: Optional[str] = Form(None),
    cargo_weight_tons: Optional[str] = Form(None),
    trip_note: Optional[str] = Form(None),
    route_segments: Optional[str] = Form(None),
    zalo_draft_token: Optional[str] = Form(None),
    zalo_payment_confirmed: bool = Form(False),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.create_cargo_trip(request=request, vehicle_id=vehicle_id, invoice_date=invoice_date, company_id=company_id, service_name=service_name, seller_company_id=seller_company_id, origin=origin, destination=destination, fuel_liters=fuel_liters, fuel_price_per_liter=fuel_price_per_liter, freight_revenue=freight_revenue, distance_km=distance_km, vat_percent=vat_percent, payment_method=payment_method, payment_status=payment_status, debt_amount=debt_amount, document_number=document_number, cargo_description=cargo_description, cargo_weight_tons=cargo_weight_tons, trip_note=trip_note, route_segments=route_segments, zalo_draft_token=zalo_draft_token, zalo_payment_confirmed=zalo_payment_confirmed, user=user)


async def edit_cargo_trip_page(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_cargo_trip_page(trip_id=trip_id, request=request, vehicle_id=vehicle_id, user=user)


async def edit_cargo_trip(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int] = None,
    invoice_date: str = Form(...),
    company_id: int = Form(...),
    service_name: str = Form(...),
    seller_company_id: Optional[int] = Form(None),
    origin: Optional[str] = Form(None),
    destination: Optional[str] = Form(None),
    fuel_liters: Optional[str] = Form(None),
    fuel_price_per_liter: Optional[str] = Form(None),
    freight_revenue: Optional[str] = Form(None),
    distance_km: Optional[str] = Form(None),
    vat_percent: Optional[str] = Form(None),
    payment_method: Optional[str] = Form(None),
    payment_status: Optional[str] = Form(None),
    debt_amount: Optional[str] = Form(None),
    document_number: Optional[str] = Form(None),
    cargo_description: Optional[str] = Form(None),
    cargo_weight_tons: Optional[str] = Form(None),
    trip_note: Optional[str] = Form(None),
    route_segments: Optional[str] = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_cargo_trip(trip_id=trip_id, request=request, vehicle_id=vehicle_id, invoice_date=invoice_date, company_id=company_id, service_name=service_name, seller_company_id=seller_company_id, origin=origin, destination=destination, fuel_liters=fuel_liters, fuel_price_per_liter=fuel_price_per_liter, freight_revenue=freight_revenue, distance_km=distance_km, vat_percent=vat_percent, payment_method=payment_method, payment_status=payment_status, debt_amount=debt_amount, document_number=document_number, cargo_description=cargo_description, cargo_weight_tons=cargo_weight_tons, trip_note=trip_note, route_segments=route_segments, user=user)


async def update_cargo_service(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    service_name: str = Form(...),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.update_cargo_service(trip_id=trip_id, vehicle_id=vehicle_id, service_name=service_name, _=_)


async def push_cargo_invoice_to_bkav(
    trip_id: int,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.push_cargo_invoice_to_bkav(trip_id=trip_id, request=request, user=user)


async def confirm_zalo_invoice_not_created(
    trip_id: int,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.confirm_zalo_invoice_not_created(trip_id=trip_id, request=request, user=user)


async def retry_zalo_invoice_notice(
    trip_id: int,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.retry_zalo_invoice_notice(trip_id=trip_id, request=request, user=user)


async def export_cargo_invoice(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_cargo_invoice(trip_id=trip_id, vehicle_id=vehicle_id, user=user)


async def export_cargo_contract(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int] = None,
    download: Optional[str] = Query(None),
    format: Optional[str] = Query(None),
    view: Optional[str] = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_cargo_contract(trip_id=trip_id, request=request, vehicle_id=vehicle_id, download=download, format=format, view=view, user=user)


async def export_cargo_hdnt(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    download: Optional[str] = Query(None),
    format: Optional[str] = Query('doc'),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_cargo_hdnt(trip_id=trip_id, vehicle_id=vehicle_id, download=download, format=format, user=user)


async def export_cargo_bbbg(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    download: Optional[str] = Query(None),
    format: Optional[str] = Query('doc'),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_cargo_bbbg(trip_id=trip_id, vehicle_id=vehicle_id, download=download, format=format, user=user)


async def export_cargo_hdnt_bbbg(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    download: Optional[str] = Query(None),
    format: Optional[str] = Query('doc'),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_cargo_hdnt_bbbg(trip_id=trip_id, vehicle_id=vehicle_id, download=download, format=format, user=user)


async def get_trip_qr_endpoint(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.get_trip_qr_endpoint(trip_id=trip_id, vehicle_id=vehicle_id, user=user)


async def generate_and_save_trip_qr_endpoint(
    trip_id: int,
    request: Request,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.generate_and_save_trip_qr_endpoint(trip_id=trip_id, request=request, vehicle_id=vehicle_id, user=user)


async def add_invoice_page(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.add_invoice_page(request=request, user=user)


async def create_invoice(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.create_invoice(request=request, user=user)


async def save_invoice_to_db(
    request: Request,
    invoice_guid: Optional[UUID] = None,
    user: dict = Depends(require_menu('vehicles')),
):
    'Save invoice form data to the database (bkav_invoices + bkav_invoice_lines).\n    If invoice_guid exists, update the existing invoice header and replace line items.\n    '
    return await controller.save_invoice_to_db(request=request, invoice_guid=invoice_guid, user=user)


async def push_invoice_form_to_bkav(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.push_invoice_form_to_bkav(request=request, user=user)


async def push_saved_invoice_to_bkav_endpoint(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.push_saved_invoice_to_bkav_endpoint(invoice_guid=invoice_guid, request=request, user=user)


async def get_saved_invoice_qr(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.get_saved_invoice_qr(invoice_guid=invoice_guid, request=request, user=user)


async def get_saved_invoice_bkav_image_info(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.get_saved_invoice_bkav_image_info(invoice_guid=invoice_guid, request=request, user=user)


async def list_invoice_zalo_groups_endpoint(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.list_invoice_zalo_groups_endpoint(invoice_guid=invoice_guid, request=request, user=user)


async def send_saved_invoice_to_zalo_endpoint(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.send_saved_invoice_to_zalo_endpoint(invoice_guid=invoice_guid, request=request, user=user)


async def recall_saved_invoice_zalo_endpoint(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.recall_saved_invoice_zalo_endpoint(invoice_guid=invoice_guid, request=request, user=user)


async def get_saved_invoice_bkav_image(
    invoice_guid: UUID,
    request: Request,
    download: int = Query(0),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.get_saved_invoice_bkav_image(invoice_guid=invoice_guid, request=request, download=download, user=user)


async def get_saved_invoice_bkav_pdf(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.get_saved_invoice_bkav_pdf(invoice_guid=invoice_guid, request=request, user=user)


async def get_trip_bkav_image_info(
    trip_id: int,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.get_trip_bkav_image_info(trip_id=trip_id, request=request, user=user)


async def export_saved_invoice(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_saved_invoice(invoice_guid=invoice_guid, request=request, user=user)


async def export_saved_invoice_dlx_excel(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_saved_invoice_dlx_excel(invoice_guid=invoice_guid, request=request, user=user)


async def export_saved_invoice_document(
    invoice_guid: UUID,
    request: Request,
    kind: str = Query('hdvc'),
    format: Optional[str] = Query(None),
    download: Optional[str] = Query(None),
    view: Optional[str] = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_saved_invoice_document(invoice_guid=invoice_guid, request=request, kind=kind, format=format, download=download, view=view, user=user)


async def edit_bkav_invoice_page(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_bkav_invoice_page(invoice_guid=invoice_guid, request=request, user=user)


async def delete_bkav_invoice_endpoint(
    invoice_guid: UUID,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.delete_bkav_invoice_endpoint(invoice_guid=invoice_guid, request=request, user=user)


async def delete_cargo_trip_endpoint(
    trip_id: int,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.delete_cargo_trip_endpoint(trip_id=trip_id, vehicle_id=vehicle_id, user=user)


async def bulk_delete_cargo_trips_endpoint(
    payload: controller.BulkDeleteTripsInput,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.bulk_delete_cargo_trips_endpoint(payload=payload, vehicle_id=vehicle_id, user=user)


async def bulk_mark_unpaid_endpoint(
    request: Request,
    payload: controller.BulkMarkUnpaidInput,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.bulk_mark_unpaid_endpoint(request=request, payload=payload, user=user)


async def scan_handwriting(
    request: Request,
    vehicle_id: Optional[int] = None,
    image: UploadFile = File(...),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.scan_handwriting(request=request, vehicle_id=vehicle_id, image=image, user=user)


async def review_cargo_scan(
    request: Request,
    token: str = Query(...),
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.review_cargo_scan(request=request, token=token, vehicle_id=vehicle_id, user=user)


async def confirm_cargo_scan(
    request: Request,
    vehicle_id: Optional[int] = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.confirm_cargo_scan(request=request, vehicle_id=vehicle_id, user=user)
