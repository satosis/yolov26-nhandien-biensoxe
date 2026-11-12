"""Cầu nối HTTP của frontend.modules.transport.tax_report_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends
from frontend.deps import require_menu
import frontend.modules.transport.tax_report_controller as controller


async def tax_report_page(
    request: Request,
    year: int = Query(None),
    quarter: int = Query(None),
    deductible_in: str | None = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.tax_report_page(request=request, year=year, quarter=quarter, deductible_in=deductible_in, user=user)


async def export_tax_report(
    request: Request,
    year: int = Query(None),
    quarter: int = Query(None),
    deductible_in: str | None = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_tax_report(request=request, year=year, quarter=quarter, deductible_in=deductible_in, user=user)
