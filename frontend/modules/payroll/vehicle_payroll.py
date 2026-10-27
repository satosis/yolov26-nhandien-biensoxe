"""Monthly driver pay calculated from vehicles and recorded cargo trips."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from backend.services.database import db
from frontend.modules.payroll.staff_payroll import payroll_rows


MEAL_ALLOWANCE_PER_WORK_DAY = 150000


def monthly_payroll_rows(start: date, end: date, vehicle_ids: set[int]) -> list[dict]:
    if not vehicle_ids:
        return []
    rows = db._execute_query(
        """SELECT v.id AS vehicle_id, v.plate_number,
                  COALESCE(u.username, d.driver_code) AS driver_code,
                  COALESCE(u.full_name, d.full_name, v.owner_name) AS driver_name,
                  COALESCE(u.full_name, d.full_name, v.owner_name) AS full_name,
                  d.hire_date, COALESCE(d.monthly_base_salary, 6000000) AS base_salary,
                  COALESCE(d.monthly_base_salary, 6000000) AS monthly_base_salary,
                  COALESCE(t.work_days, 0) AS work_days,
                  COALESCE(t.work_days, 0) * %s AS meal_allowance,
                  COALESCE(t.trip_wage, 0) AS trip_wage,
                  COALESCE(t.trip_other_cost, 0) AS trip_other_cost,
                  COALESCE(d.monthly_base_salary, 6000000)
                    + COALESCE(t.work_days, 0) * %s
                    + COALESCE(t.trip_wage, 0) + COALESCE(t.trip_other_cost, 0) AS total_salary
             FROM public.plate_whitelist v
             LEFT JOIN LATERAL (
               SELECT driver_code, full_name, hire_date, monthly_base_salary
                 FROM public.transport_drivers
                WHERE vehicle_id = v.id ORDER BY id LIMIT 1
             ) d ON TRUE
             LEFT JOIN LATERAL (
               SELECT username, full_name FROM public.users
                WHERE lower(btrim(username)) = lower(btrim(d.driver_code))
                   OR lower(btrim(full_name)) = lower(btrim(v.owner_name))
                   OR lower(btrim(username)) = lower(btrim(v.owner_name))
                ORDER BY CASE WHEN lower(btrim(username)) = lower(btrim(d.driver_code))
                              THEN 0 ELSE 1 END, id LIMIT 1
             ) u ON TRUE
             LEFT JOIN LATERAL (
               SELECT COUNT(DISTINCT COALESCE((departure_time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date,
                                              invoice_date)) AS work_days,
                      SUM(trip_wage) AS trip_wage,
                      SUM(trip_other_cost) AS trip_other_cost
                 FROM public.cargo_trips
                WHERE vehicle_id = v.id
                  AND COALESCE((departure_time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date,
                               invoice_date) >= %s
                  AND COALESCE((departure_time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date,
                               invoice_date) < %s
             ) t ON TRUE
            WHERE v.list_type = 'white' AND v.id = ANY(%s::int[])
            ORDER BY v.plate_number""",
        (MEAL_ALLOWANCE_PER_WORK_DAY, MEAL_ALLOWANCE_PER_WORK_DAY,
         start, end, list(vehicle_ids)), fetchall=True, commit=False,
    ) or []
    staff_by_username = {row["username"].casefold(): row for row in payroll_rows(start)}
    for row in rows:
        staff = staff_by_username.get(str(row.get("driver_code") or "").casefold())
        if staff is None:
            row.update(salary_source="vehicle", has_month_entry=True,
                       bonus_pay=Decimal(0), advance_amount=Decimal(0),
                       salary_deductions=Decimal(0), net_salary=row["total_salary"])
            continue
        factor = staff["work_factor"]
        base = (Decimal(staff["base_salary"]) * factor).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)
        row.update(
            salary_source="staff", has_month_entry=staff["has_month_entry"],
            base_salary=base,
            monthly_base_salary=staff["base_salary"],
            meal_allowance=staff["fixed_pay"] - base,
            work_days=staff["work_days"],
            trip_wage=staff["trip_wage"],
            bonus_pay=staff["bonus_amount"] + staff["overtime_pay"],
            advance_amount=staff["advance_amount"],
            salary_deductions=(staff["employee_insurance"] + staff["tax_deduction"]
                               + staff["other_deduction"] + staff["union_fee"]),
            total_salary=staff["gross_pay"],
            net_salary=staff["net_pay"],
        )
    return rows
