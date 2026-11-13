from typing import TypedDict, Optional


class SessionUser(TypedDict, total=False):
    id: int
    username: str
    role: str
    full_name: str
    avatar: str
    allowed_menus: str
    site_ids: list[int]
    company_ids: list[int]


class InvoiceCompany(TypedDict, total=False):
    id: int
    name: str
    tax_code: str
    address: str
    email: Optional[str]

