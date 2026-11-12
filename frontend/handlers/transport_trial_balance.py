"""Cầu nối HTTP của frontend.modules.transport.trial_balance_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends
from frontend.deps import require_menu
import frontend.modules.transport.trial_balance_controller as controller


async def trial_balance_page(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    mode: str = Query('all'),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.trial_balance_page(request=request, year=year, period=period, date_from=date_from, date_to=date_to, mode=mode, user=user)


async def export_trial_balance(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    mode: str = Query('all'),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_trial_balance(request=request, year=year, period=period, date_from=date_from, date_to=date_to, mode=mode, user=user)


async def income_statement_page(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.income_statement_page(request=request, year=year, period=period, date_from=date_from, date_to=date_to, user=user)


async def export_income_statement(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_income_statement(request=request, year=year, period=period, date_from=date_from, date_to=date_to, user=user)


async def balance_sheet_page(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.balance_sheet_page(request=request, year=year, period=period, date_from=date_from, date_to=date_to, user=user)


async def export_balance_sheet(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_balance_sheet(request=request, year=year, period=period, date_from=date_from, date_to=date_to, user=user)


async def financial_notes_page(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.financial_notes_page(request=request, year=year, period=period, date_from=date_from, date_to=date_to, user=user)


async def export_financial_notes(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_financial_notes(request=request, year=year, period=period, date_from=date_from, date_to=date_to, user=user)


async def api_account_ledger(
    request: Request,
    code: str = Query(...),
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_account_ledger(request=request, code=code, year=year, period=period, date_from=date_from, date_to=date_to, user=user)


async def export_ledger(
    request: Request,
    code: str = Query(...),
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_ledger(request=request, code=code, year=year, period=period, date_from=date_from, date_to=date_to, user=user)


async def add_trial_balance_account(request: Request, user: dict = Depends(require_menu('vehicles'))):
    'Add a new custom account to F01-DNN trial balance.'
    return await controller.add_trial_balance_account(request=request, user=user)


async def edit_trial_balance_account(
    request: Request,
    code: str,
    user: dict = Depends(require_menu('vehicles')),
):
    'Edit account name and opening balances.'
    return await controller.edit_trial_balance_account(request=request, code=code, user=user)


async def delete_trial_balance_account(
    request: Request,
    code: str,
    user: dict = Depends(require_menu('vehicles')),
):
    'Delete a custom account or reset override.'
    return await controller.delete_trial_balance_account(request=request, code=code, user=user)
