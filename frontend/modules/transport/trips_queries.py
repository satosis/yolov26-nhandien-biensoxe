"""Truy vấn cơ sở dữ liệu của frontend.modules.transport.trips_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db
from frontend.context.company_context import get_current_company_id
from psycopg2.extras import RealDictCursor
from psycopg2.extras import Json
from frontend.modules.invoices.export import DEFAULT_SERVICE_NAME
from decimal import Decimal
from frontend.modules.invoices.debt_service import adjust_company_debt
import logging

logger = logging.getLogger(__name__)


def count_cargo_history_groups_select_cargo_trips(trip_where, invoice_where, trip_params, invoice_params):
    return db._execute_query(f"""
        SELECT
            (SELECT COUNT(*) FROM public.cargo_trips trip {trip_where}) +
            (SELECT COUNT(*) FROM public.bkav_invoices invoice {invoice_where}) AS count
    """, tuple([*trip_params, *invoice_params]), fetch=True, commit=False)


def get_cargo_history_groups_select_cargo_trips(trip_where, invoice_where, limit, offset, trip_params, invoice_params):
    return db._execute_query(f"""
        SELECT kind, record_key, invoice_date, departure_time FROM (
            SELECT 'trip' AS kind, trip.id::text AS record_key,
                   trip.invoice_date, trip.invoice_bill_number AS invoice_number, trip.departure_time
            FROM public.cargo_trips trip {trip_where}
            UNION ALL
            SELECT 'invoice' AS kind, invoice.invoice_guid::text AS record_key,
                   invoice.invoice_date, invoice.invoice_number::bigint, NULL::timestamptz AS departure_time
            FROM public.bkav_invoices invoice {invoice_where}
        ) history
        ORDER BY invoice_date DESC NULLS LAST, invoice_number DESC NULLS LAST,
                 departure_time DESC NULLS LAST, record_key DESC
        LIMIT %s OFFSET %s
    """, tuple([*trip_params, *invoice_params, limit, offset]), fetchall=True, commit=False)


def get_cargo_history_groups_select_cargo_trips_2(trip_ids):
    return db._execute_query("""
        SELECT trip.*, COALESCE(trip.plate_number, vehicle.plate_number, '') AS plate_number,
               company.name AS company_name, seller.name AS seller_company_name
        FROM public.cargo_trips trip
        LEFT JOIN public.plate_whitelist vehicle ON vehicle.id = trip.vehicle_id
        LEFT JOIN public.invoice_companies company ON company.id = trip.company_id
        LEFT JOIN public.invoice_companies seller ON seller.id = trip.seller_company_id
        WHERE trip.id = ANY(%s)
    """, (trip_ids,), fetchall=True, commit=False)


def get_cargo_history_groups_select_bkav_invoices(guids):
    return db._execute_query("""
        SELECT invoice.*, buyer.name AS company_name, seller.name AS seller_company_name
        FROM public.bkav_invoices invoice
        LEFT JOIN public.invoice_companies buyer ON buyer.id = invoice.buyer_company_id
        LEFT JOIN public.invoice_companies seller ON seller.id = invoice.seller_company_id
        WHERE invoice.invoice_guid = ANY(%s::uuid[])
    """, (guids,), fetchall=True, commit=False)


def get_cargo_history_groups_select_bkav_invoice_lines(guids):
    return db._execute_query("""
        SELECT line.*, vehicle.plate_number AS plate_number
        FROM public.bkav_invoice_lines line
        LEFT JOIN public.plate_whitelist vehicle ON vehicle.id = line.vehicle_id
        WHERE line.invoice_guid = ANY(%s::uuid[])
        ORDER BY line.invoice_guid, line.line_number
    """, (guids,), fetchall=True, commit=False)


def get_default_seller_company_id_select_transport_drivers(vehicle_id):
    return db._execute_query(
        "SELECT driver_code, full_name FROM transport_drivers WHERE vehicle_id = %s LIMIT 1",
        (vehicle_id,), fetch=True, commit=False
    )


def get_default_seller_company_id_select_users(driver_code):
    return db._execute_query(
        "SELECT id, username, full_name, company_ids FROM users WHERE username = %s LIMIT 1",
        (driver_code,), fetch=True, commit=False
    )


def get_default_seller_company_id_select_users_2(driver_name):
    return db._execute_query(
        "SELECT id, username, full_name, company_ids FROM users WHERE LOWER(full_name) = LOWER(%s) OR LOWER(username) = LOWER(%s) LIMIT 1",
        (str(driver_name).strip(), str(driver_name).strip()), fetch=True, commit=False
    )


def get_default_seller_company_id_select_users_3(current_user):
    return db._execute_query(
        "SELECT company_ids FROM users WHERE id = %s",
        (current_user["id"],), fetch=True, commit=False
    )


def update_cargo_trip_update_cargo_trips(vehicle_id, invoice_date, company_id, origin, destination, fuel_liters, fuel_price_per_liter, freight_revenue, distance_km, vat_rate, payment_method, payment_status, debt_amount, service_name, document_number, driver_name, cargo_description, trip_quantity, cargo_weight_tons, trip_wage, trip_other_cost, trip_note, odometer_end, vehicle_owner_type, outsourced_freight, seller_company_id, trip_id, route_segments):
    return db._execute_query(
        """UPDATE cargo_trips SET vehicle_id = %s, invoice_date = %s, company_id = %s,
           origin = %s, destination = %s, fuel_liters = %s,
           fuel_price_per_liter = %s, freight_revenue = %s,
           distance_km = %s, vat_rate = %s, payment_method = %s,
           payment_status = %s, debt_amount = %s, service_name = %s,
           document_number = %s, driver_name = %s, cargo_description = %s,
           trip_quantity = %s, cargo_weight_tons = %s, trip_wage = %s,
           trip_other_cost = %s, trip_note = %s, odometer_end = %s,
           vehicle_owner_type = %s, outsourced_freight = %s,
           seller_company_id = %s, route_segments = %s, updated_at = NOW() WHERE id = %s""",
        (vehicle_id, invoice_date, company_id, origin, destination, fuel_liters,
         fuel_price_per_liter, freight_revenue, distance_km,
         vat_rate, payment_method, payment_status, debt_amount, service_name,
         document_number, driver_name, cargo_description, trip_quantity, cargo_weight_tons,
         trip_wage, trip_other_cost, trip_note, odometer_end, vehicle_owner_type, outsourced_freight,
         seller_company_id, Json(route_segments or []), trip_id),
        commit=True,
    )


def get_cargo_trip_select_cargo_trips(trip_id):
    return db._execute_query("SELECT * FROM cargo_trips WHERE id = %s", (trip_id,), fetch=True, commit=False)


def get_next_document_number_select_cargo_trips():
    return db._execute_query(
        """SELECT COALESCE(
            MAX(NULLIF(regexp_replace(document_number, '[^0-9]', '', 'g'), '')::bigint),
            0
        ) AS max_num FROM cargo_trips""",
        fetch=True,
        commit=False,
    )


def ensure_invoice_bill_number_update_cargo_trips(trip_id, vid):
    return db._execute_query(
        """UPDATE public.cargo_trips
               SET invoice_bill_number = COALESCE(
                   invoice_bill_number, nextval('public.cargo_invoice_bill_number_seq')
               )
               WHERE id = %s AND vehicle_id = %s
               RETURNING invoice_bill_number""",
        (trip_id, vid),
        fetch=True,
        commit=True,
    )


def ensure_invoice_bill_number_update_cargo_trips_2(trip_id):
    return db._execute_query(
        """UPDATE public.cargo_trips
               SET invoice_bill_number = COALESCE(
                   invoice_bill_number, nextval('public.cargo_invoice_bill_number_seq')
               )
               WHERE id = %s
               RETURNING invoice_bill_number""",
        (trip_id,),
        fetch=True,
        commit=True,
    )


def ensure_invoice_bill_number_update_cargo_trips_3(bill_num, trip_id, vid):
    return db._execute_query(
        "UPDATE public.cargo_trips SET invoice_bill_number = %s WHERE id = %s AND vehicle_id = %s",
        (bill_num, trip_id, vid),
        commit=True,
    )


def ensure_invoice_bill_number_update_cargo_trips_4(bill_num, trip_id):
    return db._execute_query(
        "UPDATE public.cargo_trips SET invoice_bill_number = %s WHERE id = %s",
        (bill_num, trip_id),
        commit=True,
    )


def delete_cargo_trip_delete_cargo_trips(trip_id, vehicle_id):
    return db._execute_query(
        "DELETE FROM public.cargo_trips WHERE id = %s AND vehicle_id = %s",
        (trip_id, vehicle_id),
        commit=True,
    )


def delete_cargo_trip_delete_cargo_trips_2(trip_id):
    return db._execute_query(
        "DELETE FROM public.cargo_trips WHERE id = %s",
        (trip_id,),
        commit=True,
    )


def select_vehicle_by_id(vehicle_id):
    return db.select("plate_whitelist", conditions={"id": vehicle_id}, fetchall=False)


def crane_transport_tax_rule_select_invoice_tax_rules(invoice_day):
    return db._execute_query(
        """SELECT vat_rate, source_ref, source_url, valid_to
           FROM public.invoice_tax_rules
           WHERE service_code = %s AND valid_from <= %s AND valid_to >= %s
           ORDER BY valid_from DESC LIMIT 1""",
        ("CRANE_TRANSPORT_DOMESTIC", invoice_day, invoice_day),
        fetch=True,
        commit=False,
    )


def bkav_sync_context_select_plate_whitelist(payload):
    return db.select("plate_whitelist", conditions={"id": payload.vehicle_id}, fetchall=False)


def cargo_trips_page_select_cargo_trips(debt_sum_params, trip_debt_where):
    return db._execute_query(
        f"SELECT COALESCE(SUM(trip.debt_amount), 0) AS total_debt "
        f"FROM cargo_trips trip {trip_debt_where}",
        tuple(debt_sum_params), fetch=True, commit=False
    )


def cargo_trips_page_select_bkav_invoices(invoice_debt_params, invoice_debt_where):
    return db._execute_query(
        f"SELECT COALESCE(SUM(invoice.debt_amount), 0) AS total_debt "
        f"FROM public.bkav_invoices invoice {invoice_debt_where}",
        tuple(invoice_debt_params), fetch=True, commit=False,
    )


def select_vehicles_by_plate(of_company=True):
    conditions = {"company_id": get_current_company_id()} if of_company else None
    return db.select("plate_whitelist", conditions=conditions, order_by="plate_number ASC")


def update_cargo_service_update_cargo_trips(next_cargo, next_note, trip_id, service_name):
    return db._execute_query(
        """UPDATE public.cargo_trips SET service_name = %s,
           cargo_description = %s, trip_note = %s, updated_at = NOW()
           WHERE id = %s""",
        (service_name.strip(), next_cargo, next_note, trip_id),
        commit=True,
    )


def select_trip_of_vehicle(trip_id, vehicle_id):
    return db._execute_query("SELECT * FROM cargo_trips WHERE id = %s AND vehicle_id = %s", (trip_id, vehicle_id), fetch=True, commit=False)


def update_trip_qr_url(qr_url, trip_id):
    return db._execute_query("UPDATE cargo_trips SET qr_url = %s, updated_at = NOW() WHERE id = %s", (qr_url, trip_id), commit=True)


def generate_and_save_trip_qr_endpoint_update_cargo_trips(note, trip_id):
    return db._execute_query("UPDATE cargo_trips SET trip_note = %s WHERE id = %s", (note, trip_id), commit=True)


def generate_and_save_trip_qr_endpoint_update_cargo_trips_3(val, trip_id):
    return db._execute_query("UPDATE cargo_trips SET debt_amount = %s WHERE id = %s", (val, trip_id), commit=True)


def saved_invoice_for_action_select_bkav_invoices(invoice_guid):
    return db._execute_query("""
        SELECT * FROM public.bkav_invoices WHERE invoice_guid = %s
    """, (str(invoice_guid),), fetch=True, commit=False)


def saved_invoice_for_action_select_bkav_invoice_lines(invoice_guid):
    return db._execute_query("""
        SELECT line.*, vehicle.plate_number AS plate_number
        FROM public.bkav_invoice_lines line
        LEFT JOIN public.plate_whitelist vehicle ON vehicle.id = line.vehicle_id
        WHERE line.invoice_guid = %s ORDER BY line.line_number
    """, (str(invoice_guid),), fetchall=True, commit=False)


def get_trip_bkav_image_info_select_bkav_invoice_imports(trip_id):
    return db._execute_query(
        "SELECT invoice_guid FROM public.bkav_invoice_imports WHERE trip_id = %s",
        (trip_id,), fetch=True, commit=False,
    )


def get_trip_bkav_image_info_select_bkav_saved_invoice_links(trip_id):
    return db._execute_query(
        "SELECT invoice_guid FROM public.bkav_saved_invoice_links WHERE partner_invoice_id = %s",
        (trip_id,), fetch=True, commit=False,
    )


def get_trip_bkav_image_info_select_bkav_invoices(target_guid):
    return db._execute_query(
        "SELECT 1 FROM public.bkav_invoices WHERE invoice_guid = %s",
        (str(target_guid),), fetch=True, commit=False,
    )


def get_trip_bkav_image_info_select_bkav_invoice_pushes(trip_id):
    return db._execute_query(
        "SELECT invoice_guid FROM public.bkav_invoice_pushes WHERE trip_id = %s",
        (trip_id,), fetch=True, commit=False,
    )


def edit_bkav_invoice_page_select_bkav_invoices(invoice_guid):
    return db._execute_query("""
        SELECT invoice.*, buyer.name AS buyer_name
        FROM public.bkav_invoices invoice
        LEFT JOIN public.invoice_companies buyer ON buyer.id = invoice.buyer_company_id
        WHERE invoice.invoice_guid = %s
    """, (str(invoice_guid),), fetch=True, commit=False)


def edit_bkav_invoice_page_select_bkav_invoice_lines(invoice_guid):
    return db._execute_query("""
        SELECT line.*, vehicle.plate_number AS plate_number,
               vehicle.fuel_consumption
        FROM public.bkav_invoice_lines line
        LEFT JOIN public.plate_whitelist vehicle ON vehicle.id = line.vehicle_id
        WHERE line.invoice_guid = %s ORDER BY line.line_number
    """, (str(invoice_guid),), fetchall=True, commit=False)


def select_invoices_to_delete(guids, seller_company_id=None, vehicle_id=None):
    """Các hóa đơn trong danh sách guid thuộc đúng công ty bán và (nếu có) có dòng của xe đã chọn."""
    filter_query = (
        "SELECT invoice_guid::text AS invoice_guid FROM public.bkav_invoices "
        "WHERE invoice_guid = ANY(%s::uuid[])"
    )
    params = [guids]
    if seller_company_id is not None:
        filter_query += " AND seller_company_id = %s"
        params.append(seller_company_id)
    if vehicle_id is not None:
        filter_query += (
            " AND EXISTS (SELECT 1 FROM public.bkav_invoice_lines l "
            "WHERE l.invoice_guid = bkav_invoices.invoice_guid AND l.vehicle_id = %s)"
        )
        params.append(vehicle_id)
    return db._execute_query(filter_query, tuple(params), fetchall=True, commit=False)


def scan_handwriting_select_plate_whitelist_2():
    return db.select("plate_whitelist", order_by="plate_number ASC", limit=1, fetchall=False)


def add_cargo_trip(vehicle_id: int, plate_number: str, departure_time,
                   return_time=None, invoice_date=None, company_id=None,
                   origin=None, destination=None,
                   fuel_liters=None, fuel_price_per_liter=None,
                   freight_revenue=None, distance_km=None, vat_rate=None,
                   payment_method=None, payment_status="paid", debt_amount=0,
                   service_name=DEFAULT_SERVICE_NAME, document_number=None, driver_name=None,
                   cargo_description=None, trip_quantity=1, cargo_weight_tons=None,
                   trip_wage=None, trip_other_cost=None, trip_note=None, odometer_end=None,
                   vehicle_owner_type=None, outsourced_freight=None, seller_company_id=None,
                   route_segments=None) -> int | None:
    query = """
        INSERT INTO cargo_trips
            (vehicle_id, plate_number, departure_time, return_time, invoice_date, company_id,
             origin, destination,
             fuel_liters, fuel_price_per_liter,
             freight_revenue, distance_km, vat_rate, payment_method,
             payment_status, debt_amount, service_name,
             document_number, driver_name, cargo_description, trip_quantity, cargo_weight_tons,
             trip_wage, trip_other_cost, trip_note, odometer_end, vehicle_owner_type, outsourced_freight, seller_company_id,
             route_segments)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """
    trip_id = db._execute_query(
        query,
        (vehicle_id, plate_number, departure_time, return_time, invoice_date, company_id,
         origin, destination,
         fuel_liters, fuel_price_per_liter,
         freight_revenue, distance_km, vat_rate, payment_method,
         payment_status, debt_amount, service_name,
         document_number, driver_name, cargo_description, trip_quantity, cargo_weight_tons,
         trip_wage, trip_other_cost, trip_note, odometer_end, vehicle_owner_type, outsourced_freight, seller_company_id,
         Json(route_segments or [])),
        commit=True, return_id=True
    )
    if trip_id is None:
        logger.error("Cargo trip insert returned no ID for vehicle %s", vehicle_id)
    elif company_id and payment_status == "debt":
        d_amt = Decimal(str(debt_amount or 0))
        if d_amt > 0:
            adjust_company_debt(
                company_id=company_id,
                amount=d_amt,
                owner_company_id=seller_company_id,
                debt_type="trip_debt",
                note=f"Nợ chuyến hàng {document_number or trip_id}",
                reference_doc=str(trip_id),
            )
    return trip_id


def add_reviewed_cargo_trips(vehicle_id: int, plate_number: str, trips: list[dict], seller_company_id: int | None = None) -> bool:
    if not trips:
        return True
    vehicle = select_vehicle_by_id(vehicle_id)
    columns = (
        "vehicle_id", "plate_number", "invoice_date", "departure_time", "return_time", "origin",
        "destination", "fuel_liters", "fuel_price_per_liter", "freight_revenue", "distance_km", "driver_name", "seller_company_id",
    )
    placeholders = ", ".join(["(" + ", ".join(["%s"] * len(columns)) + ")"] * len(trips))
    values = tuple(
        value
        for trip in trips
        for value in (vehicle_id, plate_number, trip["departure_time"].date(), *(trip[column] for column in columns[3:-2]), vehicle["owner_name"], seller_company_id)
    )
    query = "INSERT INTO cargo_trips (" + ", ".join(columns) + ") VALUES " + placeholders + " RETURNING id"
    rows = db._execute_query(query, values, fetchall=True, commit=True)
    if not isinstance(rows, list) or len(rows) != len(trips):
        logger.error(
            "Cargo trip bulk insert returned %s IDs for %s rows on vehicle %s",
            len(rows) if isinstance(rows, list) else 0, len(trips), vehicle_id,
        )
        return False
    return True


def select_drivers_with_vehicle():
    return db._execute_query(
        "SELECT vehicle_id, full_name, driver_code FROM public.transport_drivers WHERE vehicle_id IS NOT NULL",
        fetchall=True,
    )


def select_invoice_company_by_tax_code(tax_code):
    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM public.invoice_companies WHERE tax_code = %s LIMIT 1", (tax_code,))
            return cursor.fetchone()


def insert_invoice_company(name, tax_code):
    with db._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                INSERT INTO public.invoice_companies (name, tax_code)
                VALUES (%s, %s)
                RETURNING *
            """, (name, tax_code))
            buyer = cursor.fetchone()
        conn.commit()
    return buyer


def update_invoice_company_tax_code(tax_code, company_id):
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE public.invoice_companies SET tax_code = %s WHERE id = %s", (tax_code, company_id))
        conn.commit()


def delete_cargo_trips(trip_ids, vehicle_id=None):
    """Xóa các chuyến (chỉ của xe đã chọn nếu có); trả số chuyến đã xóa."""
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.cargo_trips WHERE id = ANY(%s) "
                + ("AND vehicle_id = %s " if vehicle_id is not None else "")
                + "RETURNING id",
                (trip_ids, vehicle_id) if vehicle_id is not None else (trip_ids,),
            )
            count = len(cursor.fetchall())
        conn.commit()
    return count
