"""The company an enterprise page works for."""

from typing import Optional

from fastapi import Request

from backend.services.database import db
from frontend.modules.invoices.companies import get_company


def get_active_company(request: Request, ctx: dict, company_id: Optional[int] = None) -> dict | None:
    """The requested company, else the session's, else the user's first, else the first one; remembered in the session."""
    target_cid = company_id or ctx.get("active_company_id")
    company = get_company(target_cid) if target_cid else None
    user_comps = ctx.get("user_companies") or []

    if not company and user_comps:
        company = get_company(user_comps[0]["id"])

    if not company:
        first_comp = db._execute_query(
            "SELECT id FROM public.invoice_companies ORDER BY id LIMIT 1",
            fetch=True, commit=False
        )
        if first_comp:
            company = get_company(first_comp["id"])

    if company and request.session.get("active_company_id") != company["id"]:
        request.session["active_company_id"] = company["id"]
        ctx["active_company_id"] = company["id"]
        ctx["active_company"] = company

    return company
