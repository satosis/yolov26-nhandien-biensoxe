"""Truy vấn cơ sở dữ liệu của frontend.modules.transport.report_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db
from frontend.context.company_context import get_current_company_id


def vehicle_module_options_select_plate_whitelist(site_id):
    return db._execute_query(
        """SELECT id, plate_number FROM public.plate_whitelist
           WHERE list_type = 'white'
             AND (%s::int IS NULL OR site_ids @> jsonb_build_array(%s::int)
                  OR site_ids = '[]'::jsonb OR site_ids IS NULL)
             AND company_id = %s
           ORDER BY plate_number""",
        (site_id, site_id, get_current_company_id()), fetchall=True, commit=False,
    )


def monthly_vehicle_rows_select_cargo_trips(start, end, site_id):
    return db._execute_query(
        """WITH trips AS (
             SELECT vehicle_id, COUNT(*) AS trip_count,
                    COALESCE(SUM(freight_revenue), 0) AS revenue_net,
                    COALESCE(SUM(ROUND(freight_revenue * (1 + COALESCE(vat_rate, 0)), 0)), 0) AS revenue,
                    COALESCE(SUM(fuel_cost_total), 0) AS estimated_fuel,
                    COALESCE(SUM(distance_km), 0) AS distance_km,
                    COALESCE(SUM(trip_wage), 0) AS trip_wage,
                    COALESCE(SUM(trip_other_cost), 0) AS trip_other_cost,
                    COALESCE(SUM(outsourced_freight), 0) AS outsourced_freight
               FROM (
                   SELECT vehicle_id, freight_revenue, vat_rate, fuel_cost_total,
                          distance_km, trip_wage, trip_other_cost, outsourced_freight,
                          invoice_date
                   FROM public.cargo_trips
                   UNION ALL
                   SELECT line.vehicle_id, line.amount AS freight_revenue,
                          COALESCE(line.tax_amount / NULLIF(line.amount, 0), invoice.vat_rate) AS vat_rate,
                          line.fuel_cost_total, line.distance_km,
                          NULL::numeric AS trip_wage, NULL::numeric AS trip_other_cost,
                          NULL::numeric AS outsourced_freight, invoice.invoice_date
                   FROM public.bkav_invoice_lines line
                   JOIN public.bkav_invoices invoice ON invoice.invoice_guid = line.invoice_guid
               ) activity
              WHERE invoice_date >= %s AND invoice_date < %s
              GROUP BY vehicle_id
           ), expenses AS (
             SELECT vehicle_id,
                    COUNT(*) AS expense_entries,
                    COALESCE(SUM(amount) FILTER (WHERE expense_type = 'fuel'), 0) AS fuel,
                    COUNT(*) FILTER (WHERE expense_type = 'fuel' AND amount IS NOT NULL) AS fuel_entries,
                    COUNT(*) FILTER (WHERE expense_type = 'repair' AND amount IS NOT NULL) AS repair_entries,
                    COUNT(*) FILTER (WHERE expense_type = 'toll' AND amount IS NOT NULL) AS toll_entries,
                    COUNT(*) FILTER (WHERE amount IS NULL) AS missing_expense_amounts,
                    COALESCE(SUM(amount) FILTER (WHERE expense_type = 'repair'), 0) AS repair,
                    COALESCE(SUM(amount) FILTER (WHERE expense_type = 'toll'), 0) AS toll,
                    COALESCE(SUM(amount) FILTER (WHERE expense_type IN ('other', 'inspection', 'insurance')), 0) AS other
               FROM public.vehicle_expenses
              WHERE expense_date >= %s AND expense_date < %s
              GROUP BY vehicle_id
           )
           SELECT v.id, v.plate_number, v.owner_name, v.vehicle_type, v.purchase_price,
                  v.depreciation_start_date, v.depreciation_years,
                  COALESCE(t.trip_count, 0) AS trip_count,
                  COALESCE(t.revenue, 0) AS revenue,
                  COALESCE(t.revenue_net, 0) AS revenue_net,
                  COALESCE(t.estimated_fuel, 0) AS estimated_fuel,
                  COALESCE(t.distance_km, 0) AS distance_km,
                  COALESCE(t.trip_wage, 0) AS trip_wage,
                  COALESCE(t.trip_other_cost, 0) AS trip_other_cost,
                  COALESCE(t.outsourced_freight, 0) AS outsourced_freight,
                  COALESCE(e.fuel, 0) AS purchased_fuel,
                  COALESCE(e.expense_entries, 0) AS expense_entries,
                  COALESCE(e.fuel_entries, 0) AS fuel_entries,
                  COALESCE(e.repair_entries, 0) AS repair_entries,
                  COALESCE(e.toll_entries, 0) AS toll_entries,
                  COALESCE(e.missing_expense_amounts, 0) AS missing_expense_amounts,
                  COALESCE(e.repair, 0) AS repair,
                  COALESCE(e.toll, 0) AS toll,
                  COALESCE(e.other, 0) AS other,
                  0 AS payroll_entries,
                  0::numeric AS base_salary,
                  0::numeric AS meal_allowance,
                  0::numeric AS bonus_pay,
                  0::numeric AS salary_gross
             FROM public.plate_whitelist v
             LEFT JOIN trips t ON t.vehicle_id = v.id
             LEFT JOIN expenses e ON e.vehicle_id = v.id
            WHERE v.list_type = 'white'
              AND (%s::int IS NULL OR v.site_ids @> jsonb_build_array(%s::int)
                   OR v.site_ids = '[]'::jsonb OR v.site_ids IS NULL)
            ORDER BY v.plate_number""",
        (start, end, start, end, site_id, site_id), fetchall=True, commit=False,
    )


def monthly_report_select_cargo_trips(vehicle_id, start, end):
    return db._execute_query(
        """SELECT t.id::text AS id, 'trip' AS record_type, t.invoice_date, t.document_number, t.origin, t.destination,
                  t.cargo_description, t.distance_km, t.freight_revenue, t.vat_rate,
                  ROUND(t.freight_revenue * (1 + COALESCE(t.vat_rate, 0)), 0) AS invoice_total,
                  t.trip_wage, t.trip_other_cost, t.payment_status, t.debt_amount,
                  c.name AS company_name
             FROM public.cargo_trips t
             LEFT JOIN public.invoice_companies c ON c.id = t.company_id
            WHERE t.vehicle_id = %s AND t.invoice_date >= %s AND t.invoice_date < %s
            UNION ALL
            SELECT i.invoice_guid::text AS id, 'invoice' AS record_type, i.invoice_date,
                   lpad(i.invoice_number::text, 8, '0') AS document_number,
                   l.origin, l.destination, l.cargo_description, l.distance_km,
                   l.amount AS freight_revenue, i.vat_rate,
                   l.amount + COALESCE(l.tax_amount, ROUND(l.amount * COALESCE(i.vat_rate, 0), 0)) AS invoice_total,
                   NULL::numeric AS trip_wage, NULL::numeric AS trip_other_cost,
                   i.payment_status, i.debt_amount, c.name AS company_name
              FROM public.bkav_invoice_lines l
              JOIN public.bkav_invoices i ON i.invoice_guid = l.invoice_guid
              LEFT JOIN public.invoice_companies c ON c.id = i.buyer_company_id
             WHERE l.vehicle_id = %s AND i.invoice_date >= %s AND i.invoice_date < %s
            ORDER BY invoice_date, id""",
        (vehicle_id, start, end, vehicle_id, start, end), fetchall=True, commit=False,
    )


def vehicle_expenses_run_sql(expense_params, summary_select, expense_filters):
    return db._execute_query(
        f"{summary_select} {expense_filters} GROUP BY e.expense_type",
        expense_params, fetchall=True, commit=False,
    )


def vehicle_expenses_select_sql(expense_params, expense_filters):
    return db._execute_query(
        f"SELECT COUNT(*) AS total {expense_filters}", expense_params,
        fetch=True, commit=False,
    )


def vehicle_expenses_select_sql_2(expense_filters, expense_params, page_data):
    return db._execute_query(
        f"""SELECT e.*, v.plate_number, (e.source_ref LIKE 'input_invoice:%%') AS from_invoice {expense_filters}
            ORDER BY e.expense_date DESC, e.id DESC LIMIT %s OFFSET %s""",
        (*expense_params, page_data["per_page"], (page_data["page"] - 1) * page_data["per_page"]),
        fetchall=True, commit=False,
    )


def add_vehicle_expense_select_plate_whitelist(vehicle_id):
    return db._execute_query("SELECT id FROM public.plate_whitelist WHERE id = %s", (vehicle_id,),
                                fetch=True, commit=False)


def add_vehicle_expense_insert_vehicle_expenses(vehicle_id, day, expense_type, value, liters, price, vendor, invoice_number, note):
    return db._execute_query(
        """INSERT INTO public.vehicle_expenses
             (vehicle_id, expense_date, expense_type, amount, quantity, unit_price, vendor, invoice_number, note)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (vehicle_id, day, expense_type, value, liters, price,
         vendor.strip() or None, invoice_number.strip() or None, note.strip() or None),
        commit=True,
    )


def delete_vehicle_expense_delete_vehicle_expenses(expense_id):
    return db._execute_query("DELETE FROM public.vehicle_expenses WHERE id = %s", (expense_id,), commit=True)


def update_vehicle_expense_amount_update_vehicle_expenses(value, expense_id):
    return db._execute_query(
        "UPDATE public.vehicle_expenses SET amount = %s WHERE id = %s",
        (value, expense_id), commit=True,
    )
