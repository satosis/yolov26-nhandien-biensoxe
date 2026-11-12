"""Cầu nối HTTP của frontend.routers.vehicles_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
from pydantic import PositiveInt
from typing import List, Optional
import frontend.routers.vehicles_controller as controller


async def vehicles_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.vehicles_page(request=request, page=page, user=user)


async def add_vehicle_page(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.add_vehicle_page(request=request, user=user)


async def edit_vehicle_page(
    vehicle_id: int,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_vehicle_page(vehicle_id=vehicle_id, request=request, user=user)


async def add_vehicle(
    request: Request,
    plate_number: str = Form(...),
    site_ids: List[PositiveInt] = Form(default=[]),
    owner_name: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    fuel_consumption: Optional[str] = Form(None),
    vehicle_type: Optional[str] = Form(None),
    purchase_price: Optional[str] = Form(None),
    depreciation_start_date: Optional[str] = Form(None),
    depreciation_years: Optional[str] = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.add_vehicle(request=request, plate_number=plate_number, site_ids=site_ids, owner_name=owner_name, note=note, fuel_consumption=fuel_consumption, vehicle_type=vehicle_type, purchase_price=purchase_price, depreciation_start_date=depreciation_start_date, depreciation_years=depreciation_years, user=user)


async def delete_vehicle_post(plate_id: int, request: Request, _: dict = Depends(require_menu('vehicles'))):
    return await controller.delete_vehicle_post(plate_id=plate_id, request=request, _=_)


async def edit_vehicle(
    vehicle_id: int,
    plate_number: str = Form(...),
    site_ids: List[PositiveInt] = Form(default=[]),
    owner_name: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    fuel_consumption: Optional[str] = Form(None),
    vehicle_type: Optional[str] = Form(None),
    purchase_price: Optional[str] = Form(None),
    depreciation_start_date: Optional[str] = Form(None),
    depreciation_years: Optional[str] = Form(None),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_vehicle(vehicle_id=vehicle_id, plate_number=plate_number, site_ids=site_ids, owner_name=owner_name, note=note, fuel_consumption=fuel_consumption, vehicle_type=vehicle_type, purchase_price=purchase_price, depreciation_start_date=depreciation_start_date, depreciation_years=depreciation_years, _=_)
