"""Toàn bộ route của ứng dụng, chỉ khai báo: phương thức, đường dẫn, hàm xử lý.

Logic nằm trong các module handler; thêm hoặc đổi URL chỉ sửa ở đây. Thứ tự các dòng là thứ tự khớp URL,
nên đường dẫn cụ thể phải đứng trước đường dẫn có tham số cùng gốc.
"""
from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from frontend.auth.rbac import get_current_user
import frontend.handlers.administrative_divisions as administrative_divisions
import frontend.routers.detection.pages as detection_pages
import frontend.handlers.detection_stream as detection_stream
import frontend.handlers.detection_video_api as detection_video_api
import frontend.handlers.detection_webrtc as detection_webrtc
import frontend.handlers.detection_ocr_api as detection_ocr_api
import frontend.handlers.cameras as cameras
import frontend.handlers.vehicles as vehicles
import frontend.handlers.transport_report as transport_report
import frontend.handlers.payroll as payroll
import frontend.handlers.invoices as invoices
import frontend.handlers.transport_tax_report as transport_tax_report
import frontend.handlers.transport_depreciation as transport_depreciation
import frontend.handlers.transport_trial_balance as transport_trial_balance
import frontend.handlers.transport_cash_bank as transport_cash_bank
import frontend.handlers.transport_prepaid_expenses as transport_prepaid_expenses
import frontend.handlers.transport_debt_offsets as transport_debt_offsets
import frontend.handlers.transport_other_vouchers as transport_other_vouchers
import frontend.handlers.service_accounts as service_accounts
import frontend.handlers.api_keys as api_keys
import frontend.handlers.transport_trips as transport_trips
import frontend.handlers.advertising as advertising
import frontend.handlers.facebook as facebook
import frontend.handlers.listings as listings
import frontend.handlers.partners as partners
import frontend.handlers.account_sales as account_sales
import frontend.handlers.chat as chat
import frontend.handlers.claude_code as claude_code
import frontend.handlers.project_news as project_news
import frontend.handlers.prospects as prospects
import frontend.handlers.transport_contracts as transport_contracts
import frontend.handlers.transport_catalog as transport_catalog
import frontend.handlers.strangers as strangers
import frontend.handlers.pending_plates as pending_plates
import frontend.handlers.parking as parking
import frontend.handlers.recordings as recordings
import frontend.handlers.sites as sites
import frontend.handlers.invoices_companies as invoices_companies
import frontend.handlers.enterprise as enterprise
import frontend.handlers.enterprise_tax_checklist as enterprise_tax_checklist
import frontend.handlers.enterprise_supplier as enterprise_supplier
import frontend.handlers.enterprise_inventory as enterprise_inventory
import frontend.handlers.enterprise_goods_spec as enterprise_goods_spec
import frontend.handlers.enterprise_partner_trade as enterprise_partner_trade
import frontend.handlers.staff_staff as staff_staff
import frontend.handlers.providers as providers
import frontend.handlers.door_providers as door_providers
import frontend.handlers.rolling_doors as rolling_doors
import frontend.handlers.telegram as telegram
import frontend.handlers.zalo as zalo
import frontend.handlers.rbac as rbac
import frontend.handlers.users as users
import frontend.routers.system_controller as system
import frontend.handlers.inquiries as inquiries
import frontend.handlers.cms as cms
import frontend.handlers.auth as auth
import frontend.handlers.auth_profile as auth_profile
import frontend.handlers.landing as landing
import frontend.handlers.legal as legal
import frontend.modules.invoices.share_link as invoices_share_link
import frontend.catalog as catalog
import frontend.container.config as container_config
import frontend.crane.controller as crane
import frontend.photo.controller as photo
import frontend.scale.controller as scale
import frontend.solar.config as solar_config
import frontend.warehouse.controller as warehouse
import frontend.weights.controller as weights
import frontend.handlers.kyc as kyc
import frontend.handlers.reminders as reminders
import frontend.site_controller as site_handlers

HTML = {"response_class": HTMLResponse}
JSON = {"response_class": JSONResponse}
HIDDEN = {"include_in_schema": False}
AUTH = {"dependencies": [Depends(get_current_user)]}

# (phương thức, đường dẫn, hàm xử lý[, tùy chọn]); "WS" là websocket, "GET|POST" là nhiều phương thức.
ROUTES = [
    # frontend/handlers/administrative_divisions.py
    ("GET", "/admin/administrative-divisions", administrative_divisions.administrative_divisions_page),
    ("GET", "/admin/administrative_divisions", administrative_divisions.administrative_divisions_page, HIDDEN),
    ("POST", "/admin/administrative-divisions/sync", administrative_divisions.sync_administrative_divisions),
    ("POST", "/admin/administrative_divisions/sync", administrative_divisions.sync_administrative_divisions, HIDDEN),
    # frontend/routers/detection/pages.py
    ("GET", "/admin/detection", detection_pages.detection_page),
    ("GET", "/admin/detection/", detection_pages.detection_page, HIDDEN),
    # frontend/handlers/detection_stream.py
    ("GET", "/admin/detection/stream/{camera_id}", detection_stream.stream_camera),
    # frontend/handlers/detection_video_api.py
    ("POST", "/admin/detection/api/videos", detection_video_api.analyze_video, {"status_code": 202}),
    ("GET", "/admin/detection/api/videos/current", detection_video_api.current_video_analysis, HIDDEN),
    ("GET", "/admin/detection/api/videos/latest", detection_video_api.current_video_analysis),
    ("GET", "/admin/detection/api/videos/{job_id}", detection_video_api.video_analysis_status),
    ("POST", "/admin/detection/api/videos/{job_id}/cancel", detection_video_api.cancel_video_analysis),
    ("GET", "/admin/detection/api/videos/{job_id}/assets/{filename}", detection_video_api.video_analysis_crop),
    # frontend/handlers/detection_webrtc.py
    ("POST", "/admin/detection/go2rtc-proxy/{camera_id}", detection_webrtc.go2rtc_webrtc_proxy),
    # frontend/handlers/detection_ocr_api.py
    ("POST", "/admin/detection/api/detect", detection_ocr_api.detect_image),
    ("POST", "/admin/detection/api/reset-tracker", detection_ocr_api.reset_tracker),
    # frontend/handlers/cameras.py
    ("GET", "/admin/cameras", cameras.cameras_page),
    ("POST", "/admin/cameras", cameras.create_camera),
    ("DELETE", "/admin/cameras/{camera_id}", cameras.delete_camera),
    ("POST", "/admin/cameras/{camera_id:int}/toggle", cameras.toggle_camera),
    ("POST", "/admin/cameras/{camera_id}/edit", cameras.edit_camera),
    ("POST", "/admin/cameras/{camera_id}/stream/start", cameras.start_stream),
    ("POST", "/admin/cameras/{camera_id}/stream/stop", cameras.stop_stream),
    ("GET", "/admin/cameras/{camera_id}/stream/status", cameras.get_stream_status),
    ("POST", "/admin/cameras/discover", cameras.discover_cameras),
    ("POST", "/admin/cameras/auto-add", cameras.auto_add_camera),
    ("POST", "/admin/cameras/probe-onvif", cameras.probe_onvif),
    ("GET", "/admin/cameras/health", cameras.camera_health),
    # frontend/handlers/vehicles.py
    ("GET", "/admin/vehicles", vehicles.vehicles_page),
    ("GET", "/admin/vehicles/add", vehicles.add_vehicle_page),
    ("GET", "/admin/vehicles/{vehicle_id}/edit", vehicles.edit_vehicle_page),
    ("POST", "/admin/vehicles", vehicles.add_vehicle),
    ("POST", "/admin/vehicles/{plate_id}/delete", vehicles.delete_vehicle_post),
    ("POST", "/admin/vehicles/{vehicle_id}/edit", vehicles.edit_vehicle),
    # frontend/handlers/transport_report.py
    ("GET", "/admin/vehicles/monthly-report", transport_report.monthly_report),
    ("GET", "/admin/vehicles/payroll", transport_report.vehicle_payroll),
    ("GET", "/admin/vehicles/expenses", transport_report.vehicle_expenses),
    ("GET", "/admin/vehicles/monthly-report/export", transport_report.export_monthly_report),
    ("POST", "/admin/vehicles/expenses", transport_report.add_vehicle_expense),
    ("POST", "/admin/vehicles/expenses/{expense_id}/delete", transport_report.delete_vehicle_expense),
    ("POST", "/admin/vehicles/expenses/{expense_id}/amount", transport_report.update_vehicle_expense_amount),
    # frontend/handlers/payroll.py
    ("GET", "/admin/transport/payroll", payroll.staff_payroll_page),
    ("POST", "/admin/transport/payroll/timesheet/save", payroll.save_payroll_timesheet),
    ("POST", "/admin/transport/payroll/timesheet/mark-shift", payroll.mark_payroll_shift),
    ("POST", "/admin/transport/payroll/timesheet/quick-fill", payroll.quick_fill_payroll_timesheet),
    ("GET", "/admin/transport/payroll/export", payroll.export_staff_payroll),
    ("GET", "/admin/transport/payroll/{staff_id}", payroll.staff_payroll_detail_page),
    ("POST", "/admin/transport/payroll/{staff_id}/advances", payroll.advance_staff_salary),
    ("POST", "/admin/transport/payroll/{staff_id}", payroll.save_staff_payroll),
    # frontend/handlers/invoices.py
    ("GET", "/admin/transport/input-invoices", invoices.input_invoices_page),
    ("GET", "/admin/transport/input-invoices/add", invoices.add_input_invoice_page),
    ("GET", "/admin/transport/input-invoices/{invoice_id}/edit", invoices.edit_input_invoice_page),
    ("POST", "/admin/transport/input-invoices/extract", invoices.extract_input_invoice),
    ("POST", "/admin/transport/input-invoices", invoices.save_input_invoice),
    ("GET", "/admin/transport/input-invoices/{invoice_id}/file", invoices.download_input_invoice),
    ("POST", "/admin/transport/input-invoices/{invoice_id}/delete", invoices.delete_input_invoice),
    ("POST", "/admin/transport/input-invoices/{invoice_id}/vehicle", invoices.set_input_invoice_vehicle),
    ("POST", "/admin/transport/input-invoices/book-electricity", invoices.book_electricity_api),
    ("GET", "/admin/transport/input-invoices/gmail/status", invoices.gmail_status_api),
    ("GET", "/admin/transport/input-invoices/gmail/auth", invoices.gmail_auth_redirect),
    ("GET", "/admin/transport/input-invoices/gmail/callback", invoices.gmail_oauth_callback),
    ("POST", "/admin/transport/input-invoices/gmail/disconnect", invoices.gmail_disconnect_api),
    ("POST", "/admin/transport/input-invoices/gmail/sync", invoices.gmail_sync_api),
    ("POST", "/admin/transport/input-invoices/imap/sync", invoices.imap_sync_api),
    ("GET", "/admin/transport/input-invoices/saveinvoice/status", invoices.saveinvoice_status_api),
    ("POST", "/admin/transport/input-invoices/saveinvoice/preview", invoices.saveinvoice_preview_api),
    ("POST", "/admin/transport/input-invoices/saveinvoice/import", invoices.saveinvoice_import_api),
    ("POST", "/admin/transport/input-invoices/saveinvoice/repair-items", invoices.saveinvoice_repair_items_api),
    ("POST", "/admin/transport/input-invoices/saveinvoice/refresh", invoices.saveinvoice_refresh_api),
    # frontend/handlers/transport_tax_report.py
    ("GET", "/admin/transport/tax-report/", transport_tax_report.tax_report_page),
    ("GET", "/admin/transport/tax-report", transport_tax_report.tax_report_page, HIDDEN),
    ("GET", "/admin/transport/tax-report/export", transport_tax_report.export_tax_report),
    # frontend/handlers/transport_depreciation.py
    ("GET", "/admin/transport/depreciation/", transport_depreciation.depreciation_page),
    ("GET", "/admin/transport/depreciation", transport_depreciation.depreciation_page, HIDDEN),
    ("GET", "/admin/transport/depreciation/export", transport_depreciation.export_depreciation),
    ("POST", "/admin/transport/depreciation/api/add", transport_depreciation.api_add_asset),
    ("POST", "/admin/transport/depreciation/api/{source}/{source_id}/update", transport_depreciation.api_update_asset),
    ("POST", "/admin/transport/depreciation/api/{source}/{source_id}/delete", transport_depreciation.api_delete_asset),
    ("POST", "/admin/transport/depreciation/api/vehicle/{vehicle_id}/restore", transport_depreciation.api_restore_vehicle),
    ("POST", "/admin/transport/depreciation/voucher-api/{kind}/add", transport_depreciation.api_add_voucher),
    ("POST", "/admin/transport/depreciation/voucher-api/{voucher_id:int}/update", transport_depreciation.api_update_voucher),
    ("POST", "/admin/transport/depreciation/voucher-api/{voucher_id:int}/delete", transport_depreciation.api_delete_voucher),
    # frontend/handlers/transport_trial_balance.py
    ("GET", "/admin/transport/trial-balance/", transport_trial_balance.trial_balance_page),
    ("GET", "/admin/transport/trial-balance", transport_trial_balance.trial_balance_page, HIDDEN),
    ("GET", "/admin/transport/trial-balance/export", transport_trial_balance.export_trial_balance),
    ("GET", "/admin/transport/trial-balance/income-statement", transport_trial_balance.income_statement_page),
    ("GET", "/admin/transport/trial-balance/income-statement/export", transport_trial_balance.export_income_statement),
    ("GET", "/admin/transport/trial-balance/balance-sheet", transport_trial_balance.balance_sheet_page),
    ("GET", "/admin/transport/trial-balance/balance-sheet/export", transport_trial_balance.export_balance_sheet),
    ("GET", "/admin/transport/trial-balance/financial-notes", transport_trial_balance.financial_notes_page),
    ("GET", "/admin/transport/trial-balance/financial-notes/export", transport_trial_balance.export_financial_notes),
    ("GET", "/admin/transport/trial-balance/api/ledger", transport_trial_balance.api_account_ledger),
    ("GET", "/admin/transport/trial-balance/export-ledger", transport_trial_balance.export_ledger),
    ("POST", "/admin/transport/trial-balance/accounts/add", transport_trial_balance.add_trial_balance_account),
    ("POST", "/admin/transport/trial-balance/accounts/{code}/edit", transport_trial_balance.edit_trial_balance_account),
    ("POST", "/admin/transport/trial-balance/accounts/{code}/delete", transport_trial_balance.delete_trial_balance_account),
    # frontend/handlers/transport_cash_bank.py
    ("GET", "/admin/transport/cash-book/", transport_cash_bank.cash_book_page),
    ("GET", "/admin/transport/cash-book", transport_cash_bank.cash_book_page, HIDDEN),
    ("GET", "/admin/transport/cash-book/export", transport_cash_bank.cash_book_export),
    ("GET", "/admin/transport/cash-book/{entry_id}/print", transport_cash_bank.cash_voucher_print),
    ("GET", "/admin/transport/bank-book/", transport_cash_bank.bank_book_page),
    ("GET", "/admin/transport/bank-book", transport_cash_bank.bank_book_page, HIDDEN),
    ("GET", "/admin/transport/bank-book/export", transport_cash_bank.bank_book_export),
    ("POST", "/admin/transport/api/cash-bank/entry", transport_cash_bank.api_add_cash_bank_entry),
    ("POST", "/admin/transport/api/cash-bank/special-payment", transport_cash_bank.api_special_payment),
    ("POST", "/admin/transport/api/cash-bank/bank-interest", transport_cash_bank.api_bank_interest),
    ("POST", "/admin/transport/api/cash-bank/cash-transfer", transport_cash_bank.api_cash_transfer),
    ("POST", "/admin/transport/api/cash-bank/renumber-vouchers", transport_cash_bank.api_renumber_vouchers),
    ("GET", "/admin/transport/api/cash-bank/payroll-preview", transport_cash_bank.api_payroll_preview),
    ("GET", "/admin/transport/api/cash-bank/open-invoices", transport_cash_bank.api_open_receivable_invoices),
    ("GET", "/admin/transport/api/cash-bank/open-payables", transport_cash_bank.api_open_payable_invoices),
    ("GET", "/admin/transport/api/cash-bank/entry/{entry_id}", transport_cash_bank.api_get_cash_bank_entry),
    ("POST", "/admin/transport/api/cash-bank/entry/{entry_id}/update", transport_cash_bank.api_update_cash_bank_entry),
    ("POST", "/admin/transport/api/cash-bank/entry/{entry_id}/delete", transport_cash_bank.api_delete_cash_bank_entry),
    ("GET", "/admin/transport/bank-book/template", transport_cash_bank.bank_book_download_template),
    ("POST", "/admin/transport/bank-book/import-excel", transport_cash_bank.bank_book_import_excel),
    ("GET", "/admin/transport/cash-book/template", transport_cash_bank.cash_book_download_template),
    ("POST", "/admin/transport/cash-book/import-excel", transport_cash_bank.cash_book_import_excel),
    ("POST", "/admin/transport/api/cash-bank/opening-balance", transport_cash_bank.api_set_opening_balance),
    ("GET", "/admin/transport/api/cash-bank/opening-balance", transport_cash_bank.api_get_opening_balance),
    # frontend/handlers/transport_prepaid_expenses.py
    ("GET", "/admin/transport/prepaid-expenses/", transport_prepaid_expenses.prepaid_expenses_page),
    ("GET", "/admin/transport/prepaid-expenses", transport_prepaid_expenses.prepaid_expenses_page, HIDDEN),
    ("GET", "/admin/transport/prepaid-expenses/export", transport_prepaid_expenses.prepaid_expenses_export),
    ("POST", "/admin/transport/prepaid-expenses/api/add", transport_prepaid_expenses.api_add_ccdc),
    ("POST", "/admin/transport/prepaid-expenses/api/{item_id}/update", transport_prepaid_expenses.api_update_ccdc),
    ("POST", "/admin/transport/prepaid-expenses/api/{item_id}/delete", transport_prepaid_expenses.api_delete_ccdc),
    ("POST", "/admin/transport/prepaid-expenses/voucher-api/{kind}/add", transport_prepaid_expenses.api_add_voucher),
    ("POST", "/admin/transport/prepaid-expenses/voucher-api/{voucher_id:int}/update", transport_prepaid_expenses.api_update_voucher),
    ("POST", "/admin/transport/prepaid-expenses/voucher-api/{voucher_id:int}/delete", transport_prepaid_expenses.api_delete_voucher),
    # frontend/handlers/transport_debt_offsets.py
    ("GET", "/admin/transport/debt-offsets/", transport_debt_offsets.debt_offsets_page),
    ("GET", "/admin/transport/debt-offsets", transport_debt_offsets.debt_offsets_page, HIDDEN),
    ("POST", "/admin/transport/debt-offsets/", transport_debt_offsets.create_debt_offset),
    ("POST", "/admin/transport/debt-offsets", transport_debt_offsets.create_debt_offset, HIDDEN),
    ("POST", "/admin/transport/debt-offsets/{offset_id}/edit", transport_debt_offsets.edit_debt_offset),
    ("POST", "/admin/transport/debt-offsets/{offset_id}/delete", transport_debt_offsets.delete_debt_offset),
    # frontend/handlers/transport_other_vouchers.py
    ("GET", "/admin/transport/other-vouchers/", transport_other_vouchers.other_vouchers_page),
    ("GET", "/admin/transport/other-vouchers", transport_other_vouchers.other_vouchers_page, HIDDEN),
    ("POST", "/admin/transport/other-vouchers/", transport_other_vouchers.create_other_voucher_action),
    ("POST", "/admin/transport/other-vouchers", transport_other_vouchers.create_other_voucher_action, HIDDEN),
    ("POST", "/admin/transport/other-vouchers/{voucher_id}/edit", transport_other_vouchers.edit_other_voucher_action),
    ("POST", "/admin/transport/other-vouchers/{voucher_id}/delete", transport_other_vouchers.delete_other_voucher_action),
    # frontend/handlers/service_accounts.py
    ("GET", "/admin/service-accounts", service_accounts.service_accounts_page),
    ("POST", "/admin/service-accounts", service_accounts.save_account_endpoint),
    ("POST", "/admin/service-accounts/{account_id}/delete", service_accounts.delete_account_endpoint),
    ("POST", "/admin/service-accounts/{account_id}/quick-balance", service_accounts.quick_balance_endpoint),
    ("GET", "/admin/transport/service-accounts", service_accounts.alias_service_accounts_page),
    ("POST", "/admin/transport/service-accounts", service_accounts.alias_save_account),
    # frontend/handlers/api_keys.py
    ("GET", "/admin/api-keys", api_keys.api_keys_page),
    ("POST", "/admin/api-keys/entries", api_keys.save_api_keys),
    ("GET", "/admin/api_keys", api_keys.alias_api_keys),
    # frontend/handlers/transport_trips.py
    ("POST", "/admin/vehicles/cargo/invoices/preview", transport_trips.preview_bkav_cargo_invoices),
    ("POST", "/admin/vehicles/cargo/bkav/preview", transport_trips.preview_bkav_cargo_invoices, HIDDEN),
    ("POST", "/admin/vehicles/cargo/invoices/import", transport_trips.import_bkav_cargo_invoices),
    ("POST", "/admin/vehicles/cargo/bkav/import", transport_trips.import_bkav_cargo_invoices, HIDDEN),
    ("GET", "/admin/vehicles/cargo", transport_trips.cargo_trips_page),
    ("GET", "/admin/vehicles/api/invoice-companies/lookup", transport_trips.lookup_invoice_company),
    ("POST", "/admin/vehicles/api/invoice-companies/{company_id}/refresh", transport_trips.refresh_invoice_company),
    ("GET", "/admin/vehicles/api/invoice-tax-suggestion", transport_trips.invoice_tax_suggestion),
    ("POST", "/admin/vehicles/api/invoice-companies", transport_trips.create_invoice_company),
    ("GET", "/admin/vehicles/api/resolve-maps", transport_trips.resolve_maps_url),
    ("GET", "/admin/vehicles/api/geocode", transport_trips.geocode_address),
    ("GET", "/admin/vehicles/api/calculate-distance", transport_trips.calculate_distance),
    ("GET", "/admin/vehicles/api/calculate-distance-by-address", transport_trips.calculate_distance_by_address),
    ("POST", "/admin/vehicles/api/normalize-invoice-locations", transport_trips.normalize_invoice_locations),
    ("GET", "/admin/vehicles/api/google-maps-status", transport_trips.google_maps_status_api),
    ("GET", "/admin/vehicles/api/fuel-price", transport_trips.get_fuel_price_api),
    ("GET", "/admin/vehicles/api/next-invoice-number", transport_trips.get_next_invoice_number_api),
    ("GET", "/admin/vehicles/{vehicle_id}/cargo/add", transport_trips.add_cargo_trip_page),
    ("GET", "/admin/vehicles/cargo/add", transport_trips.add_cargo_trip_page),
    ("POST", "/admin/vehicles/{vehicle_id}/cargo/add", transport_trips.create_cargo_trip),
    ("POST", "/admin/vehicles/cargo/add", transport_trips.create_cargo_trip),
    ("GET", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/edit", transport_trips.edit_cargo_trip_page),
    ("GET", "/admin/vehicles/cargo/{trip_id}/edit", transport_trips.edit_cargo_trip_page),
    ("POST", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/edit", transport_trips.edit_cargo_trip),
    ("POST", "/admin/vehicles/cargo/{trip_id}/edit", transport_trips.edit_cargo_trip),
    ("POST", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/service", transport_trips.update_cargo_service),
    ("POST", "/admin/vehicles/cargo/{trip_id}/service", transport_trips.update_cargo_service),
    ("POST", "/admin/vehicles/cargo/{trip_id:int}/push-bkav", transport_trips.push_cargo_invoice_to_bkav),
    ("POST", "/admin/vehicles/cargo/{trip_id}/confirm-new-invoice", transport_trips.confirm_zalo_invoice_not_created),
    ("POST", "/admin/vehicles/cargo/{trip_id}/retry-zalo-invoice-notice", transport_trips.retry_zalo_invoice_notice),
    ("POST", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/export-invoice", transport_trips.export_cargo_invoice),
    ("POST", "/admin/vehicles/cargo/{trip_id}/export-invoice", transport_trips.export_cargo_invoice),
    ("GET", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/export-contract", transport_trips.export_cargo_contract),
    ("GET", "/admin/vehicles/cargo/{trip_id}/export-contract", transport_trips.export_cargo_contract),
    ("GET", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/export-hdnt", transport_trips.export_cargo_hdnt),
    ("GET", "/admin/vehicles/cargo/{trip_id}/export-hdnt", transport_trips.export_cargo_hdnt),
    ("GET", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/export-bbbg", transport_trips.export_cargo_bbbg),
    ("GET", "/admin/vehicles/cargo/{trip_id}/export-bbbg", transport_trips.export_cargo_bbbg),
    ("GET", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/export-hdnt-bbbg", transport_trips.export_cargo_hdnt_bbbg),
    ("GET", "/admin/vehicles/cargo/{trip_id}/export-hdnt-bbbg", transport_trips.export_cargo_hdnt_bbbg),
    ("GET", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/qr", transport_trips.get_trip_qr_endpoint),
    ("GET", "/admin/vehicles/cargo/{trip_id}/qr", transport_trips.get_trip_qr_endpoint),
    ("POST", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/qr", transport_trips.generate_and_save_trip_qr_endpoint),
    ("POST", "/admin/vehicles/cargo/{trip_id}/qr", transport_trips.generate_and_save_trip_qr_endpoint),
    ("GET", "/admin/vehicles/cargo/invoices/add", transport_trips.add_invoice_page),
    ("POST", "/admin/vehicles/cargo/invoices/add", transport_trips.create_invoice),
    ("POST", "/admin/vehicles/cargo/invoices/{invoice_guid}/save", transport_trips.save_invoice_to_db),
    ("POST", "/admin/vehicles/cargo/invoices/save", transport_trips.save_invoice_to_db),
    ("POST", "/admin/vehicles/cargo/invoices/push-bkav", transport_trips.push_invoice_form_to_bkav),
    ("POST", "/admin/vehicles/cargo/invoices/{invoice_guid}/push-bkav", transport_trips.push_saved_invoice_to_bkav_endpoint),
    ("GET", "/admin/vehicles/cargo/invoices/{invoice_guid}/qr", transport_trips.get_saved_invoice_qr),
    ("GET", "/admin/vehicles/cargo/invoices/{invoice_guid}/bkav-image-info", transport_trips.get_saved_invoice_bkav_image_info),
    ("GET", "/admin/vehicles/cargo/invoices/{invoice_guid}/zalo-groups", transport_trips.list_invoice_zalo_groups_endpoint),
    ("POST", "/admin/vehicles/cargo/invoices/{invoice_guid}/send-zalo", transport_trips.send_saved_invoice_to_zalo_endpoint),
    ("POST", "/admin/vehicles/cargo/invoices/{invoice_guid}/recall-zalo", transport_trips.recall_saved_invoice_zalo_endpoint),
    ("GET", "/admin/vehicles/cargo/invoices/{invoice_guid}/bkav-image", transport_trips.get_saved_invoice_bkav_image),
    ("GET", "/admin/vehicles/cargo/invoices/{invoice_guid}/bkav-pdf", transport_trips.get_saved_invoice_bkav_pdf),
    ("GET", "/admin/vehicles/cargo/{trip_id}/bkav-image-info", transport_trips.get_trip_bkav_image_info),
    ("GET", "/admin/vehicles/cargo/invoices/{invoice_guid}/export-invoice", transport_trips.export_saved_invoice),
    ("GET", "/admin/vehicles/cargo/invoices/{invoice_guid}/export-dlx-excel", transport_trips.export_saved_invoice_dlx_excel),
    ("GET", "/admin/vehicles/cargo/invoices/{invoice_guid}/export-document", transport_trips.export_saved_invoice_document),
    ("GET", "/admin/vehicles/cargo/invoices/{invoice_guid}/edit", transport_trips.edit_bkav_invoice_page),
    ("POST", "/admin/vehicles/cargo/invoices/{invoice_guid}/delete", transport_trips.delete_bkav_invoice_endpoint),
    ("POST", "/admin/vehicles/cargo/bkav/{invoice_guid}/delete", transport_trips.delete_bkav_invoice_endpoint, HIDDEN),
    ("POST", "/admin/vehicles/{vehicle_id}/cargo/{trip_id}/delete", transport_trips.delete_cargo_trip_endpoint),
    ("POST", "/admin/vehicles/cargo/{trip_id}/delete", transport_trips.delete_cargo_trip_endpoint),
    ("POST", "/admin/vehicles/{vehicle_id}/cargo/bulk-delete", transport_trips.bulk_delete_cargo_trips_endpoint),
    ("POST", "/admin/vehicles/cargo/bulk-delete", transport_trips.bulk_delete_cargo_trips_endpoint),
    ("POST", "/admin/vehicles/cargo/bulk-mark-unpaid", transport_trips.bulk_mark_unpaid_endpoint),
    ("POST", "/admin/vehicles/{vehicle_id}/cargo/scan", transport_trips.scan_handwriting),
    ("POST", "/admin/vehicles/cargo/scan", transport_trips.scan_handwriting),
    ("GET", "/admin/vehicles/{vehicle_id}/cargo/scan/review", transport_trips.review_cargo_scan),
    ("GET", "/admin/vehicles/cargo/scan/review", transport_trips.review_cargo_scan),
    ("POST", "/admin/vehicles/{vehicle_id}/cargo/scan/confirm", transport_trips.confirm_cargo_scan),
    ("POST", "/admin/vehicles/cargo/scan/confirm", transport_trips.confirm_cargo_scan),
    # frontend/handlers/advertising.py
    ("GET", "/admin/advertising/", advertising.advertising_dashboard_page, HTML),
    ("GET", "/admin/advertising", advertising.advertising_dashboard_page, HTML),
    ("GET", "/admin/advertising/keywords", advertising.advertising_keywords_page, HTML),
    ("POST", "/admin/advertising/api/campaigns", advertising.api_create_campaign),
    ("POST", "/admin/advertising/api/campaigns/{campaign_id}/toggle", advertising.api_toggle_campaign),
    ("POST", "/admin/advertising/api/campaigns/{campaign_id}/delete", advertising.api_delete_campaign),
    ("POST", "/admin/advertising/api/campaigns/{campaign_id}/metrics", advertising.api_update_campaign_metrics),
    ("POST", "/admin/advertising/api/campaigns/{campaign_id}/google-id", advertising.api_set_campaign_google_id),
    ("POST", "/admin/advertising/api/google/test", advertising.api_google_test_connection),
    ("POST", "/admin/advertising/api/google/sync", advertising.api_google_sync),
    ("POST", "/admin/advertising/api/google/live", advertising.api_google_set_live),
    ("POST", "/admin/advertising/api/settings", advertising.api_save_settings),
    # frontend/handlers/facebook.py
    ("GET", "/admin/advertising/facebook", facebook.facebook_page_view, HTML),
    ("POST", "/admin/advertising/facebook/health/check", facebook.facebook_run_health_check),
    ("POST", "/admin/advertising/facebook/drafts", facebook.create_draft),
    ("POST", "/admin/advertising/facebook/compose/suggest", facebook.suggest_post),
    ("POST", "/admin/advertising/facebook/drafts/generate", facebook.generate_draft),
    ("POST", "/admin/advertising/facebook/drafts/{draft_id}", facebook.update_draft),
    ("POST", "/admin/advertising/facebook/drafts/{draft_id}/approve", facebook.approve_draft),
    ("POST", "/admin/advertising/facebook/drafts/{draft_id}/reject", facebook.reject_draft),
    ("POST", "/admin/advertising/facebook/drafts/{draft_id}/delete", facebook.delete_draft),
    ("POST", "/admin/advertising/facebook/posts/{post_id}/edit", facebook.edit_post),
    ("POST", "/admin/advertising/facebook/posts/{post_id}/delete", facebook.delete_post),
    ("POST", "/admin/advertising/facebook/photos", facebook.upload_photos),
    ("POST", "/admin/advertising/facebook/photos/{photo_id:int}/delete", facebook.delete_photo),
    ("POST", "/admin/advertising/facebook/radar/settings", facebook.radar_settings),
    ("POST", "/admin/advertising/facebook/radar/draft", facebook.radar_draft),
    ("POST", "/admin/advertising/facebook/radar/lead", facebook.radar_lead),
    ("POST", "/admin/advertising/facebook/settings", facebook.save_settings),
    # frontend/handlers/listings.py
    ("GET", "/admin/advertising/listings", listings.list_page, HTML),
    ("POST", "/admin/advertising/listings/directories", listings.create_directory),
    ("POST", "/admin/advertising/listings/directories/{directory_id}", listings.update_directory),
    ("POST", "/admin/advertising/listings/directories/{directory_id}/delete", listings.delete_directory),
    ("POST", "/admin/advertising/listings/directories/{directory_id}/mark", listings.mark_directory),
    # frontend/handlers/partners.py
    ("GET", "/admin/advertising/partners", partners.list_page, HTML),
    ("POST", "/admin/advertising/partners", partners.create_partner),
    ("POST", "/admin/advertising/partners/{partner_id}", partners.update_partner),
    ("POST", "/admin/advertising/partners/{partner_id}/delete", partners.delete_partner),
    # frontend/handlers/account_sales.py
    ("GET", "/admin/account-sales/products", account_sales.products_page, HTML),
    ("POST", "/admin/account-sales/products", account_sales.create_product),
    ("POST", "/admin/account-sales/products/{product_id}", account_sales.update_product),
    ("POST", "/admin/account-sales/products/{product_id}/delete", account_sales.delete_product),
    ("GET", "/admin/account-sales/orders", account_sales.orders_page, HTML),
    ("POST", "/admin/account-sales/orders", account_sales.create_order),
    ("POST", "/admin/account-sales/orders/{order_id}/status", account_sales.update_order_status),
    # frontend/handlers/chat.py
    ("GET", "/admin/account-sales/live-chat", chat.admin_live_chat_page, HTML),
    ("GET", "/admin/account-sales/live-chat/sessions", chat.admin_list_sessions_json, JSON),
    ("GET", "/admin/account-sales/live-chat/{session_id}", chat.admin_live_chat_session_page, HTML),
    ("GET", "/admin/account-sales/live-chat/{session_id}/detail", chat.admin_get_session_detail_json, JSON),
    ("POST", "/admin/account-sales/live-chat/{session_id}/reply", chat.admin_reply, JSON),
    ("POST", "/admin/account-sales/live-chat/{session_id}/status", chat.admin_update_status, JSON),
    # frontend/handlers/claude_code.py
    ("GET", "/admin/claude-code/api/conversation", claude_code.conversation, HIDDEN),
    ("POST", "/admin/claude-code/api/conversation/new", claude_code.new_conversation, HIDDEN),
    ("POST", "/admin/claude-code/api/messages", claude_code.send, HIDDEN),
    ("GET", "/admin/claude-code/api/runs/{run_id}", claude_code.run_events, HIDDEN),
    ("POST", "/admin/claude-code/api/runs/{run_id}/stop", claude_code.stop, HIDDEN),
    # frontend/handlers/project_news.py
    ("GET", "/admin/advertising/project-news", project_news.list_page, HTML),
    ("POST", "/admin/advertising/project-news/{news_id}/save", project_news.save_news),
    ("POST", "/admin/advertising/project-news/{news_id}/skip", project_news.skip_news),
    ("POST", "/admin/advertising/project-news/queries", project_news.update_queries),
    ("POST", "/admin/advertising/project-news/refresh", project_news.refresh_news),
    # frontend/handlers/prospects.py
    ("GET", "/admin/advertising/prospects", prospects.list_page, HTML),
    ("POST", "/admin/advertising/prospects", prospects.create_prospect),
    ("POST", "/admin/advertising/prospects/import", prospects.import_prospects),
    ("POST", "/admin/advertising/prospects/{prospect_id}", prospects.update_prospect),
    ("POST", "/admin/advertising/prospects/{prospect_id}/delete", prospects.delete_prospect),
    ("POST", "/admin/advertising/prospects/{prospect_id}/status", prospects.set_prospect_status),
    # frontend/handlers/transport_contracts.py
    ("GET", "/admin/transport/contracts", transport_contracts.contracts_page),
    ("GET", "/admin/transport/contracts/export", transport_contracts.export_contract),
    # frontend/handlers/transport_catalog.py
    ("GET", "/admin/catalog", transport_catalog.cargo_catalog_page),
    ("POST", "/admin/catalog/items", transport_catalog.create_cargo_catalog_item),
    ("POST", "/admin/catalog/items/{item_id}/edit", transport_catalog.edit_cargo_catalog_item),
    ("POST", "/admin/catalog/items/{item_id}/delete", transport_catalog.remove_cargo_catalog_item),
    ("POST", "/admin/catalog/companies/{company_id}/toggle-role", transport_catalog.toggle_partner_role_endpoint),
    ("POST", "/admin/catalog/companies/{company_id}/update-opening-debts", transport_catalog.update_opening_debts_endpoint),
    ("GET", "/admin/catalog/companies/{company_id}/subledger", transport_catalog.partner_subledger_page),
    ("GET", "/admin/catalog/companies/{company_id}/subledger-data", transport_catalog.partner_subledger_data_api),
    ("GET", "/admin/transport/catalog", transport_catalog.cargo_catalog_page),
    ("POST", "/admin/transport/catalog/items", transport_catalog.create_cargo_catalog_item),
    ("POST", "/admin/transport/catalog/items/{item_id}/edit", transport_catalog.edit_cargo_catalog_item),
    ("POST", "/admin/transport/catalog/items/{item_id}/delete", transport_catalog.remove_cargo_catalog_item),
    ("POST", "/admin/transport/catalog/companies/{company_id}/toggle-role", transport_catalog.toggle_partner_role_endpoint),
    ("POST", "/admin/transport/catalog/companies/{company_id}/update-opening-debts", transport_catalog.update_opening_debts_endpoint),
    ("GET", "/admin/transport/catalog/companies/{company_id}/subledger", transport_catalog.partner_subledger_page),
    ("GET", "/admin/transport/catalog/companies/{company_id}/subledger-data", transport_catalog.partner_subledger_data_api),
    # frontend/handlers/strangers.py
    ("GET", "/admin/strangers", strangers.strangers_page),
    ("POST", "/admin/strangers/{stranger_id}/review", strangers.review_stranger),
    ("DELETE", "/admin/strangers/{stranger_id}", strangers.delete_stranger),
    ("POST", "/admin/strangers/register", strangers.register_stranger),
    ("POST", "/admin/strangers/bulk-register", strangers.bulk_register_strangers),
    ("POST", "/admin/strangers/bulk-delete", strangers.bulk_delete_strangers),
    # frontend/handlers/pending_plates.py
    ("PUT", "/admin/pending_plates/{pending_id}", pending_plates.update_pending_plate),
    ("GET", "/admin/pending_plates/{pending_id}/register", pending_plates.register_page),
    ("POST", "/admin/pending_plates/{pending_id}/register", pending_plates.register_plate),
    ("GET", "/admin/pending_plates", pending_plates.pending_page),
    ("POST", "/admin/pending_plates/bulk-reject", pending_plates.bulk_reject_plates),
    ("POST", "/admin/pending_plates/{pending_id}/reject", pending_plates.resolve_plate),
    ("POST", "/admin/pending_plates/{pending_id}/approve", pending_plates.resolve_plate),
    # frontend/handlers/parking.py
    ("GET", "/admin/parking_dashboard", parking.dashboard),
    ("GET", "/admin/parking_logs", parking.logs),
    ("PUT", "/admin/parking/logs/{session_id}", parking.update_parking_history_plate),
    ("DELETE", "/admin/parking/logs/{session_id}", parking.delete_parking_history),
    ("GET", "/admin/parking_analytics", parking.analytics),
    # frontend/handlers/recordings.py
    ("GET", "/admin/recordings", recordings.recordings_page),
    ("DELETE", "/admin/recordings/{recording_id}", recordings.delete_recording),
    # frontend/handlers/sites.py
    ("GET", "/admin/sites", sites.sites_page),
    ("POST", "/admin/sites", sites.create_site),
    ("GET", "/admin/sites/{site_id}/edit", sites.edit_site_form),
    ("GET", "/admin/sites/{site_id}/row", sites.get_site_row),
    ("POST", "/admin/sites/{site_id}/edit", sites.update_site),
    ("DELETE", "/admin/sites/{site_id}", sites.delete_site),
    ("POST", "/admin/sites/{site_id}/toggle", sites.toggle_site_status),
    # frontend/handlers/invoices_companies.py
    ("GET", "/admin/companies", invoices_companies.companies_management_page),
    ("GET", "/admin/catalog/companies/add", invoices_companies.add_company_page),
    ("GET", "/admin/transport/catalog/companies/add", invoices_companies.add_company_page),
    ("GET", "/admin/companies/add", invoices_companies.add_company_page),
    ("POST", "/admin/catalog/companies", invoices_companies.create_catalog_company),
    ("POST", "/admin/transport/catalog/companies", invoices_companies.create_catalog_company),
    ("POST", "/admin/companies", invoices_companies.create_catalog_company),
    ("GET", "/admin/catalog/companies/{company_id}/edit", invoices_companies.edit_company_page),
    ("GET", "/admin/transport/catalog/companies/{company_id}/edit", invoices_companies.edit_company_page),
    ("GET", "/admin/companies/{company_id}/edit", invoices_companies.edit_company_page),
    ("POST", "/admin/catalog/companies/{company_id}/edit", invoices_companies.edit_catalog_company),
    ("POST", "/admin/transport/catalog/companies/{company_id}/edit", invoices_companies.edit_catalog_company),
    ("POST", "/admin/companies/{company_id}/edit", invoices_companies.edit_catalog_company),
    ("POST", "/admin/catalog/companies/{company_id}/refresh", invoices_companies.refresh_catalog_company),
    ("POST", "/admin/transport/catalog/companies/{company_id}/refresh", invoices_companies.refresh_catalog_company),
    ("POST", "/admin/companies/{company_id}/refresh", invoices_companies.refresh_catalog_company),
    ("POST", "/admin/catalog/companies/{company_id}/delete", invoices_companies.remove_catalog_company),
    ("POST", "/admin/transport/catalog/companies/{company_id}/delete", invoices_companies.remove_catalog_company),
    ("POST", "/admin/companies/{company_id}/delete", invoices_companies.remove_catalog_company),
    ("POST", "/admin/catalog/companies/bulk-delete", invoices_companies.bulk_delete_catalog_companies),
    ("POST", "/admin/transport/catalog/companies/bulk-delete", invoices_companies.bulk_delete_catalog_companies),
    ("POST", "/admin/companies/bulk-delete", invoices_companies.bulk_delete_catalog_companies),
    ("POST", "/admin/catalog/companies/bulk-refresh", invoices_companies.bulk_refresh_catalog_companies),
    ("POST", "/admin/transport/catalog/companies/bulk-refresh", invoices_companies.bulk_refresh_catalog_companies),
    ("POST", "/admin/companies/bulk-refresh", invoices_companies.bulk_refresh_catalog_companies),
    ("POST", "/admin/catalog/companies/{company_id}/pay-debt", invoices_companies.pay_company_debt_endpoint),
    ("POST", "/admin/transport/catalog/companies/{company_id}/pay-debt", invoices_companies.pay_company_debt_endpoint),
    ("POST", "/admin/companies/{company_id}/pay-debt", invoices_companies.pay_company_debt_endpoint),
    ("POST", "/admin/catalog/companies/{company_id}/add-debt", invoices_companies.add_company_debt_endpoint),
    ("POST", "/admin/transport/catalog/companies/{company_id}/add-debt", invoices_companies.add_company_debt_endpoint),
    ("POST", "/admin/companies/{company_id}/add-debt", invoices_companies.add_company_debt_endpoint),
    ("GET", "/admin/catalog/companies/{company_id}/debts", invoices_companies.get_company_debts_endpoint),
    ("GET", "/admin/transport/catalog/companies/{company_id}/debts", invoices_companies.get_company_debts_endpoint),
    ("GET", "/admin/companies/{company_id}/debts", invoices_companies.get_company_debts_endpoint),
    ("POST", "/admin/companies/debts/{log_id}/delete", invoices_companies.delete_company_debt_log_endpoint),
    ("POST", "/admin/catalog/companies/{company_id}/upload-signature", invoices_companies.upload_company_signature),
    ("POST", "/admin/transport/catalog/companies/{company_id}/upload-signature", invoices_companies.upload_company_signature),
    ("POST", "/admin/catalog/companies/{company_id}/upload-stamp", invoices_companies.upload_company_stamp),
    ("POST", "/admin/transport/catalog/companies/{company_id}/upload-stamp", invoices_companies.upload_company_stamp),
    ("POST", "/admin/catalog/companies/{company_id}/remove-signature", invoices_companies.remove_company_signature),
    ("POST", "/admin/transport/catalog/companies/{company_id}/remove-signature", invoices_companies.remove_company_signature),
    ("POST", "/admin/catalog/companies/{company_id}/remove-stamp", invoices_companies.remove_company_stamp),
    ("POST", "/admin/transport/catalog/companies/{company_id}/remove-stamp", invoices_companies.remove_company_stamp),
    ("GET", "/admin/catalog/companies/{company_id}/export-contract", invoices_companies.export_company_contract),
    ("GET", "/admin/transport/catalog/companies/{company_id}/export-contract", invoices_companies.export_company_contract),
    # frontend/handlers/enterprise.py
    ("GET", "/admin/enterprise/", enterprise.enterprise_page),
    ("GET", "/admin/enterprise", enterprise.enterprise_page),
    ("POST", "/admin/enterprise/", enterprise.update_enterprise),
    ("POST", "/admin/enterprise", enterprise.update_enterprise),
    ("POST", "/admin/enterprise/refresh", enterprise.refresh_enterprise),
    ("GET", "/admin/enterprise/departments", enterprise.departments_page),
    ("POST", "/admin/enterprise/departments/create", enterprise.department_create),
    ("POST", "/admin/enterprise/departments/{dept_id}/edit", enterprise.department_edit),
    ("POST", "/admin/enterprise/departments/{dept_id}/delete", enterprise.department_delete),
    ("GET", "/admin/enterprise/attendance", enterprise.attendance_page),
    ("POST", "/admin/enterprise/attendance/save-row", enterprise.attendance_save_row),
    ("POST", "/admin/enterprise/attendance/quick-fill", enterprise.attendance_quick_fill),
    ("POST", "/admin/enterprise/attendance/leave-request/create", enterprise.leave_request_create),
    ("POST", "/admin/enterprise/attendance/leave-request/{req_id}/status", enterprise.leave_request_update_status),
    ("POST", "/admin/enterprise/attendance/leave-request/{req_id}/delete", enterprise.leave_request_delete),
    ("GET", "/admin/enterprise/advances", enterprise.advances_page),
    ("POST", "/admin/enterprise/advances/create", enterprise.advance_create),
    ("POST", "/admin/enterprise/advances/{adv_id}/settle", enterprise.advance_settle),
    ("POST", "/admin/enterprise/advances/{adv_id}/status", enterprise.advance_update_status),
    ("POST", "/admin/enterprise/advances/{adv_id}/delete", enterprise.advance_delete),
    ("GET", "/admin/enterprise/assets", enterprise.assets_page),
    ("POST", "/admin/enterprise/assets/create", enterprise.asset_assignment_create),
    ("POST", "/admin/enterprise/assets/{assign_id}/return", enterprise.asset_assignment_return),
    ("POST", "/admin/enterprise/assets/{assign_id}/delete", enterprise.asset_assignment_delete),
    ("GET", "/admin/enterprise/assets/api", enterprise.enterprise_assets_api),
    ("POST", "/admin/enterprise/assets/api", enterprise.enterprise_asset_create_api),
    ("POST", "/admin/enterprise/assets/api/{asset_id}/update", enterprise.enterprise_asset_update_api),
    ("POST", "/admin/enterprise/assets/api/{asset_id}/delete", enterprise.enterprise_asset_delete_api),
    ("GET", "/admin/enterprise/debts", enterprise.enterprise_debts_page),
    ("POST", "/admin/enterprise/debts/add", enterprise.enterprise_debt_add),
    ("POST", "/admin/enterprise/debts/pay", enterprise.enterprise_debt_pay),
    ("POST", "/admin/enterprise/debts/{log_id}/delete", enterprise.enterprise_debt_delete),
    ("PUT", "/admin/enterprise/debts/{log_id}", enterprise.enterprise_debt_update),
    ("GET", "/admin/enterprise/debts/{company_id}/logs", enterprise.enterprise_debt_company_logs),
    ("POST", "/admin/enterprise/debts/individuals/import/preview", enterprise.personal_debt_import_preview_api),
    ("POST", "/admin/enterprise/debts/individuals/import", enterprise.personal_debt_import_api),
    ("GET", "/admin/enterprise/debts/individuals", enterprise.personal_debt_customers_api),
    ("POST", "/admin/enterprise/debts/individuals/customers/merge-duplicates", enterprise.personal_debt_customers_merge_duplicates),
    ("POST", "/admin/enterprise/debts/individuals/customers", enterprise.personal_debt_customer_create),
    ("POST", "/admin/enterprise/debts/individuals/customers/{customer_id}/edit", enterprise.personal_debt_customer_edit),
    ("POST", "/admin/enterprise/debts/individuals/customers/{customer_id}/delete", enterprise.personal_debt_customer_delete),
    ("POST", "/admin/enterprise/debts/individuals/transactions", enterprise.personal_debt_transaction_create),
    ("GET", "/admin/enterprise/debts/individuals/{customer_id}/logs", enterprise.personal_debt_customer_logs),
    ("PUT", "/admin/enterprise/debts/individuals/transactions/{log_id}", enterprise.personal_debt_transaction_edit),
    ("POST", "/admin/enterprise/debts/individuals/transactions/{log_id}/delete", enterprise.personal_debt_transaction_delete),
    ("GET", "/admin/enterprise/sales", enterprise.enterprise_sales_page),
    ("GET", "/admin/enterprise/sales/api/next-stt", enterprise.enterprise_sales_next_stt_api),
    ("GET", "/admin/enterprise/sales/api", enterprise.enterprise_sales_list_api),
    ("GET", "/admin/enterprise/sales/api/items", enterprise.enterprise_sales_items_api),
    ("GET", "/admin/enterprise/sales/api/items/export", enterprise.enterprise_sales_items_export_api),
    ("POST", "/admin/enterprise/sales/api/{sale_id}/status", enterprise.enterprise_sales_update_status_api),
    ("GET", "/admin/enterprise/sales/api/{sale_id}", enterprise.enterprise_sales_get_api),
    ("POST", "/admin/enterprise/sales/api", enterprise.enterprise_sales_create_api),
    ("PUT", "/admin/enterprise/sales/api/{sale_id}", enterprise.enterprise_sales_update_api),
    ("DELETE", "/admin/enterprise/sales/api/{sale_id}", enterprise.enterprise_sales_delete_api),
    ("POST", "/admin/enterprise/sales/import/preview", enterprise.enterprise_sales_import_preview_api),
    ("POST", "/admin/enterprise/sales/import", enterprise.enterprise_sales_import_api),
    ("POST", "/admin/enterprise/sales/api/{sale_id}/payments", enterprise.enterprise_sales_payment_api),
    ("GET", "/admin/enterprise/sales/{sale_id}/pdf", enterprise.enterprise_sales_invoice_pdf),
    # frontend/handlers/enterprise_tax_checklist.py
    ("GET", "/admin/enterprise/tax-checklist/", enterprise_tax_checklist.tax_checklist_page),
    ("GET", "/admin/enterprise/tax-checklist", enterprise_tax_checklist.tax_checklist_page, HIDDEN),
    ("POST", "/admin/enterprise/tax-checklist/cycle-status", enterprise_tax_checklist.cycle_status_api),
    ("POST", "/admin/enterprise/tax-checklist/cycle-group-status", enterprise_tax_checklist.cycle_group_status_api),
    ("POST", "/admin/enterprise/tax-checklist/set-status", enterprise_tax_checklist.set_status_api),
    ("POST", "/admin/enterprise/tax-checklist/update-note", enterprise_tax_checklist.update_note_api),
    ("POST", "/admin/enterprise/tax-checklist/client/add", enterprise_tax_checklist.add_client_api),
    ("POST", "/admin/enterprise/tax-checklist/task/toggle", enterprise_tax_checklist.toggle_task_api),
    ("POST", "/admin/enterprise/tax-checklist/task/add", enterprise_tax_checklist.add_task_api),
    ("GET", "/admin/enterprise/tax-checklist/export-excel", enterprise_tax_checklist.export_excel_api),
    # frontend/handlers/enterprise_supplier.py
    ("GET", "/admin/enterprise/suppliers/", enterprise_supplier.suppliers_page, HTML),
    ("GET", "/admin/enterprise/suppliers", enterprise_supplier.suppliers_page, HTML),
    ("GET", "/admin/enterprise/suppliers/api/next-stt", enterprise_supplier.api_next_purchase_stt),
    ("GET", "/admin/enterprise/suppliers/api/purchases", enterprise_supplier.api_list_purchases),
    ("POST", "/admin/enterprise/suppliers/api/purchases", enterprise_supplier.api_create_purchase),
    ("GET", "/admin/enterprise/suppliers/api/purchases/order", enterprise_supplier.api_get_purchase_order),
    ("PUT", "/admin/enterprise/suppliers/api/purchases/order", enterprise_supplier.api_save_purchase_order),
    ("PUT", "/admin/enterprise/suppliers/api/purchases/{purchase_id}", enterprise_supplier.api_update_purchase),
    ("POST", "/admin/enterprise/suppliers/api/purchases/renumber", enterprise_supplier.api_renumber_purchases),
    ("DELETE", "/admin/enterprise/suppliers/api/purchases/{purchase_id}", enterprise_supplier.api_delete_purchase),
    ("GET", "/admin/enterprise/suppliers/api/debts", enterprise_supplier.api_list_debts),
    ("POST", "/admin/enterprise/suppliers/api/opening-debt", enterprise_supplier.api_save_opening_debt),
    ("POST", "/admin/enterprise/suppliers/api/payments", enterprise_supplier.api_record_payment),
    ("PUT", "/admin/enterprise/suppliers/api/payments/{payment_id}", enterprise_supplier.api_update_payment),
    ("DELETE", "/admin/enterprise/suppliers/api/payments/{payment_id}", enterprise_supplier.api_delete_payment),
    ("GET", "/admin/enterprise/suppliers/api/statement/{supplier_name}", enterprise_supplier.api_get_statement),
    ("POST", "/admin/enterprise/suppliers/api/import", enterprise_supplier.api_import_excel),
    ("GET", "/admin/enterprise/suppliers/export", enterprise_supplier.export_purchases),
    # frontend/handlers/enterprise_inventory.py
    ("GET", "/admin/enterprise/inventory/", enterprise_inventory.inventory_page, HTML),
    ("GET", "/admin/enterprise/inventory", enterprise_inventory.inventory_page, HTML),
    ("POST", "/admin/enterprise/inventory/items", enterprise_inventory.api_create_item),
    ("POST", "/admin/enterprise/inventory/items/{item_id}", enterprise_inventory.api_update_item),
    ("POST", "/admin/enterprise/inventory/items/{item_id}/delete", enterprise_inventory.api_delete_item),
    ("POST", "/admin/enterprise/inventory/items/{item_id}/merge", enterprise_inventory.api_merge_item),
    ("POST", "/admin/enterprise/inventory/movements", enterprise_inventory.api_create_movement),
    ("POST", "/admin/enterprise/inventory/movements/{movement_id}", enterprise_inventory.api_update_movement),
    ("POST", "/admin/enterprise/inventory/movements/{movement_id}/delete", enterprise_inventory.api_delete_movement),
    ("POST", "/admin/enterprise/inventory/import", enterprise_inventory.api_import_excel),
    ("GET", "/admin/enterprise/inventory/api/check", enterprise_inventory.api_check_stock),
    ("GET", "/admin/enterprise/inventory/api/suggest", enterprise_inventory.api_suggest_items),
    ("GET", "/admin/enterprise/inventory/template", enterprise_inventory.download_import_template),
    # frontend/handlers/enterprise_goods_spec.py
    ("GET", "/admin/enterprise/inventory/specs", enterprise_goods_spec.specs_page, HTML),
    ("POST", "/admin/enterprise/inventory/specs/items", enterprise_goods_spec.api_create_spec),
    ("POST", "/admin/enterprise/inventory/specs/items/{spec_id}", enterprise_goods_spec.api_update_spec),
    ("POST", "/admin/enterprise/inventory/specs/items/{spec_id}/delete", enterprise_goods_spec.api_delete_spec),
    ("POST", "/admin/enterprise/inventory/specs/photos", enterprise_goods_spec.api_add_photo),
    ("POST", "/admin/enterprise/inventory/specs/photos/{photo_id}", enterprise_goods_spec.api_update_photo),
    ("POST", "/admin/enterprise/inventory/specs/photos/{photo_id}/delete", enterprise_goods_spec.api_delete_photo),
    ("POST", "/admin/enterprise/inventory/specs/import", enterprise_goods_spec.api_import_excel),
    # frontend/handlers/enterprise_partner_trade.py
    ("GET", "/admin/enterprise/partner-trades/", enterprise_partner_trade.partner_trades_page, HTML),
    ("GET", "/admin/enterprise/partner-trades", enterprise_partner_trade.partner_trades_page, HTML),
    ("POST", "/admin/enterprise/partner-trades/partners/save", enterprise_partner_trade.save_partner),
    ("POST", "/admin/enterprise/partner-trades/partners/{partner_id}/delete", enterprise_partner_trade.delete_partner),
    ("POST", "/admin/enterprise/partner-trades/partners/{partner_id}/share-link", enterprise_partner_trade.get_or_create_share_link),
    ("POST", "/admin/enterprise/partner-trades/entries/save", enterprise_partner_trade.save_entry),
    ("POST", "/admin/enterprise/partner-trades/entries/{entry_id}/delete", enterprise_partner_trade.delete_entry),
    ("GET", "/admin/enterprise/partner-trades/entries/{entry_id}/detail", enterprise_partner_trade.entry_detail_page, HTML),
    ("POST", "/admin/enterprise/partner-trades/entries/{entry_id}/details", enterprise_partner_trade.save_entry_details),
    # frontend/handlers/staff_staff.py
    ("GET", "/admin/staff/", staff_staff.staff_index),
    ("GET", "/admin/staff", staff_staff.staff_index),
    ("GET", "/admin/staff/{user_id}/json", staff_staff.get_staff_json),
    ("POST", "/admin/staff/create", staff_staff.create_staff),
    ("POST", "/admin/staff/{user_id}/edit", staff_staff.update_staff),
    ("POST", "/admin/staff/{user_id}/delete", staff_staff.delete_staff),
    ("GET", "/admin/staff/{user_id}/records", staff_staff.get_staff_records),
    ("POST", "/admin/staff/{user_id}/contracts/create", staff_staff.create_staff_contract),
    ("POST", "/admin/staff/contracts/{contract_id}/delete", staff_staff.delete_staff_contract),
    ("POST", "/admin/staff/{user_id}/licenses/create", staff_staff.create_staff_license),
    ("POST", "/admin/staff/licenses/{license_id}/delete", staff_staff.delete_staff_license),
    ("POST", "/admin/staff/{user_id}/work-history/create", staff_staff.create_staff_work_history),
    ("POST", "/admin/staff/work-history/{hist_id}/delete", staff_staff.delete_staff_work_history),
    # frontend/handlers/providers.py
    ("GET", "/admin/providers", providers.providers_page),
    ("GET", "/admin/providers/{provider_key}/api-keys", providers.edit_api_keys_page),
    ("POST", "/admin/providers/{provider_key}/api-keys", providers.save_api_keys),
    ("POST", "/admin/providers", providers.upsert_provider),
    ("DELETE", "/admin/providers/{provider_key}", providers.delete_provider),
    ("POST", "/admin/providers/{provider_key}/toggle", providers.toggle_provider),
    # frontend/handlers/door_providers.py
    ("GET", "/admin/door_providers", door_providers.door_providers_page),
    ("POST", "/admin/door_providers", door_providers.upsert_door_provider_route),
    ("DELETE", "/admin/door_providers/{provider_key}", door_providers.delete_door_provider_route),
    ("POST", "/admin/door_providers/{provider_key}/toggle", door_providers.toggle_door_provider),
    # frontend/handlers/rolling_doors.py
    ("POST", "/admin/rolling_doors/scan", rolling_doors.scan_rolling_door_devices),
    ("POST", "/admin/rolling_doors/hunonic/devices", rolling_doors.hunonic_devices),
    ("GET", "/admin/rolling_doors", rolling_doors.rolling_doors_page),
    ("POST", "/admin/rolling_doors/tuya_global", rolling_doors.update_tuya_global),
    ("POST", "/admin/rolling_doors", rolling_doors.upsert_rolling_door),
    ("GET", "/admin/rolling_doors/{door_id}/edit", rolling_doors.edit_rolling_door_page),
    ("POST", "/admin/rolling_doors/{door_id}/delete", rolling_doors.delete_rolling_door),
    ("POST", "/admin/rolling_doors/{door_id}/control", rolling_doors.control_rolling_door),
    # frontend/handlers/telegram.py
    ("GET", "/admin/telegram", telegram.telegram_page),
    ("POST", "/admin/telegram", telegram.add_bot),
    ("POST", "/admin/telegram/{bot_name}/test", telegram.test_bot),
    ("DELETE", "/admin/telegram/{bot_name}", telegram.delete_bot),
    # frontend/handlers/zalo.py
    ("GET", "/admin/zalo_inbox", zalo.inbox_page),
    ("POST", "/admin/zalo_inbox/trip-draft", zalo.create_trip_draft),
    ("POST", "/admin/zalo_inbox/connect", zalo.connect_bridge),
    ("POST", "/admin/zalo_inbox/disconnect", zalo.disconnect_bridge),
    ("GET", "/admin/zalo_inbox/drafts/{draft_id}/apply", zalo.apply_saved_draft),
    ("POST", "/admin/zalo_inbox/sessions/{group_id}/finalize-now", zalo.finalize_session_now),
    ("POST", "/admin/zalo_inbox/{item_id}/retry-salary", zalo.retry_salary_message),
    ("GET", "/admin/zalo_inbox/{item_id}/file", zalo.download_file),
    ("GET", "/admin/zalo_inbox/qr", zalo.login_qr),
    ("POST", "/admin/zalo_inbox/{item_id}/reviewed", zalo.review_message),
    ("POST", "/admin/zalo_inbox/{item_id}/analyze", zalo.analyze_file),
    # frontend/handlers/rbac.py
    ("GET", "/admin/rbac", rbac.rbac_page),
    ("POST", "/admin/rbac", rbac.create_role),
    ("POST", "/admin/rbac/{role_id}/edit", rbac.update_role),
    ("DELETE", "/admin/rbac/{role_id}", rbac.delete_role),
    # frontend/handlers/users.py
    ("GET", "/admin/users", users.users_page),
    ("GET", "/admin/users/add", users.add_user_page),
    ("POST", "/admin/users", users.create_user),
    ("GET", "/admin/users/{user_id}/edit", users.edit_user_page),
    ("POST", "/admin/users/{user_id}/edit", users.edit_user),
    ("DELETE", "/admin/users/{user_id}", users.delete_user),
    ("POST", "/admin/users/bulk-delete", users.bulk_delete_users),
    ("POST", "/admin/users/{user_id}/face", users.upload_face),
    # frontend/routers/system_controller.py
    ("GET", "/admin/system/network-info", system.get_network_info, AUTH),
    # frontend/handlers/inquiries.py
    ("GET", "/admin/inquiries", inquiries.inquiries_page),
    ("POST", "/admin/inquiries/read-all", inquiries.mark_all_read, HTML),
    ("POST", "/admin/inquiries/{inquiry_id}/read", inquiries.mark_read, HTML),
    ("POST", "/admin/inquiries/{inquiry_id}/note", inquiries.save_note, HTML),
    ("DELETE", "/admin/inquiries/{inquiry_id}", inquiries.delete_inquiry_endpoint, HTML),
    # frontend/handlers/cms.py
    ("GET", "/admin/cms/", cms.cms_dashboard, {**HTML, **AUTH}),
    ("GET", "/admin/cms", cms.cms_dashboard, {**HTML, **AUTH}),
    ("POST", "/admin/cms/projects", cms.add_project, {**HTML, **AUTH}),
    ("GET", "/admin/cms/news/new", cms.news_new_page, {**HTML, **AUTH}),
    ("GET", "/admin/cms/news/{news_id}/edit", cms.news_edit_page, {**HTML, **AUTH}),
    ("POST", "/admin/cms/news/save", cms.news_save, {**HTML, **AUTH}),
    ("DELETE", "/admin/cms/projects/{project_id}", cms.delete_project, {**HTML, **AUTH}),
    ("DELETE", "/admin/cms/news/{news_id}", cms.delete_news, {**HTML, **AUTH}),
    ("POST", "/admin/cms/projects/update/{project_id}", cms.update_project, {**HTML, **AUTH}),
    ("POST", "/admin/cms/upload-image", cms.upload_image, AUTH),
    # frontend/handlers/auth.py
    ("GET", "/login", auth.login_page),
    ("POST", "/login", auth.login_submit),
    ("GET", "/register", auth.register_page),
    ("POST", "/register", auth.register_submit),
    ("GET", "/forgot-password", auth.forgot_password_page),
    ("POST", "/forgot-password", auth.forgot_password_submit),
    ("GET", "/login/google", auth.auth_google),
    ("GET", "/auth/google", auth.auth_google),
    ("GET", "/auth/google/callback", auth.auth_google_callback),
    ("GET", "/login/facebook", auth.auth_facebook),
    ("GET", "/auth/facebook", auth.auth_facebook),
    ("GET", "/auth/facebook/callback", auth.auth_facebook_callback),
    ("GET|POST", "/logout", auth.logout),
    ("POST", "/set-site", auth.set_site),
    ("POST", "/admin/set-company", auth.set_company),
    ("POST", "/set-company", auth.set_company),
    # frontend/handlers/auth_profile.py
    ("GET", "/profile", auth_profile.profile_page),
    ("POST", "/profile", auth_profile.save_profile),
    ("POST", "/profile/password", auth_profile.change_password),
    # Thép Sơn Lâm - trang công ty (không thuộc riêng dịch vụ nào): frontend/handlers/landing.py
    ("GET", "/", landing.landing_home, HTML),
    ("GET", "/gioi-thieu", landing.landing_about, HTML),
    ("GET", "/dich-vu", landing.landing_services, HTML),
    ("GET", "/du-an", landing.landing_projects, HTML),
    ("GET", "/lien-he", landing.landing_contact, HTML),
    ("GET", "/tin-tuc/{slug}", landing.landing_news_detail, HTML),
    ("GET", "/tin-tuc", landing.landing_news, HTML),
    ("GET", "/sitemap.xml", landing.sitemap),
    ("GET", "/robots.txt", landing.robots),
    ("POST", "/lien-he", landing.contact_inquiry, JSON),
    # Dịch vụ cho thuê nhà xưởng (Thép Sơn Lâm): frontend/warehouse/controller.py
    ("GET", "/nhaxuong", warehouse.landing_warehouse, HTML),
    ("GET", "/nha-xuong-co-tiet", warehouse.landing_co_tiet, HTML),
    # Dịch vụ kiểm định cân (Thép Sơn Lâm): frontend/scale/controller.py
    ("GET", "/kiem-dinh-can", scale.landing_scale, HTML),
    # frontend/handlers/account_sales.py
    ("GET", "/tai-khoan", account_sales.storefront, HTML),
    ("POST", "/tai-khoan/profile", account_sales.update_storefront_profile, JSON),
    ("POST", "/tai-khoan/profile/password", account_sales.change_storefront_password, JSON),
    ("POST", "/tai-khoan/dat-hang", account_sales.place_order, JSON),
    ("GET|POST", "/tai-khoan/don/{code}", account_sales.order_paid, JSON),
    ("GET|POST", "/tai-khoan/gio-hang", account_sales.cart_orders, JSON),
    ("POST", "/tai-khoan/chatbot/send", account_sales.chatbot_send_message, JSON),
    ("POST", "/api/webhook/sepay", account_sales.sepay_webhook, JSON),
    # Cầu nối Zalo gọi vào bằng khóa bí mật, không có phiên đăng nhập nên không nằm dưới /admin: frontend/handlers/zalo.py
    ("GET", "/api/zalo_bridge/companies", zalo.list_companies_for_zalo_bridge),
    ("POST", "/api/zalo_bridge/ingest", zalo.ingest_message),
    # frontend/handlers/chat.py
    ("POST", "/api/chat/send", chat.customer_send_message, JSON),
    ("GET", "/api/chat/messages", chat.customer_get_messages, JSON),
    ("GET", "/chat-files/{name}", chat.chat_file),
    ("POST", "/api/chat/session", chat.sync_chat_session, JSON),
    # frontend/handlers/legal.py
    ("GET", "/chinh-sach-quyen-rieng-tu", legal.privacy_policy, HTML),
    ("GET", "/privacy-policy", legal.privacy_policy, HTML),
    ("GET", "/privacy", legal.privacy_policy, HTML),
    ("GET", "/xoa-du-lieu", legal.data_deletion_page, HTML),
    ("GET", "/huong-dan-xoa-du-lieu", legal.data_deletion_page, HTML),
    ("GET", "/user-data-deletion", legal.data_deletion_page, HTML),
    ("GET", "/data-deletion", legal.data_deletion_page, HTML),
    ("POST", "/data-deletion/request", legal.submit_deletion_request, HTML),
    ("GET", "/data-deletion/status", legal.data_deletion_status, HTML),
    ("POST", "/data-deletion/callback", legal.facebook_data_deletion_callback),
    ("POST", "/facebook/data-deletion", legal.facebook_data_deletion_callback),
    ("POST", "/api/auth/facebook/data-deletion", legal.facebook_data_deletion_callback),
    ("GET", "/data-deletion/callback", legal.facebook_data_deletion_get),
    ("GET", "/facebook/data-deletion", legal.facebook_data_deletion_get),
    ("GET", "/api/auth/facebook/data-deletion", legal.facebook_data_deletion_get),
    ("GET", "/google{code}.html", legal.google_site_verification, HIDDEN),
    # frontend/modules/invoices/share_link.py
    ("GET", "/hd/{invoice_guid}/{token}", invoices_share_link.get_public_invoice_pdf),
    # frontend/handlers/enterprise_partner_trade.py
    ("GET", "/so-doi-tac/{token}", enterprise_partner_trade.public_partner_ledger, HTML),
    ("GET", "/so-doi-tac/{token}/chi-tiet/{entry_id}", enterprise_partner_trade.public_partner_entry_detail, HTML),
    # Dịch vụ xe cẩu (Đại Lộ Xanh)
    ("GET", "/xecau", catalog.redirect_route("/xe-cau"), HTML),
    ("GET", "/xe-cau", crane.landing_crane_service, HTML),
    ("GET", "/thue-xe-cau/{area}", catalog.area_route("thue-xe-cau"), HTML),
    # Dịch vụ quả cân chuẩn (Đại Lộ Xanh)
    ("GET", "/quacan", catalog.redirect_route("/qua-can"), HTML),
    ("GET", "/qua-can", weights.landing_test_weights_service, HTML),
    ("GET", "/van-chuyen-qua-can/{area}", catalog.area_route("van-chuyen-qua-can"), HTML),
    # Dịch vụ cẩu lắp đặt thiết bị (Đại Lộ Xanh, trang khu vực dùng chung)
    ("GET", "/cau-lap-dat-thiet-bi/{area}", catalog.area_route("cau-lap-dat-thiet-bi"), HTML),
    # Dịch vụ cho thuê container (Đại Lộ Xanh)
    ("GET", "/container", catalog.redirect_route("/thue-container"), HTML),
    ("GET", "/thue-container", catalog.landing_route("thue-container", container_config.get_container_settings, container_config.PAGE), HTML),
    ("GET", "/thue-container/{area}", catalog.area_route("thue-container"), HTML),
    # Dịch vụ điện mặt trời (Đại Lộ Xanh)
    ("GET", "/solar", catalog.redirect_route("/dien-mat-troi"), HTML),
    ("GET", "/dien-mat-troi", catalog.landing_route("dien-mat-troi", solar_config.get_solar_settings, solar_config.PAGE), HTML),
    ("GET", "/dien-mat-troi/{area}", catalog.area_route("dien-mat-troi"), HTML),
    # Dịch vụ cho thuê máy ảnh và photobooth (Đại Lộ Xanh)
    ("GET", "/thue-may-anh", photo.landing_photo_service, HTML),
    ("GET", "/thue-may-anh/{area}", catalog.area_route("thue-may-anh"), HTML),
    # frontend/handlers/kyc.py
    ("POST", "/kyc/clean-cache", kyc.clean_cache),
    ("GET", "/kyc", kyc.kyc_page),
    ("POST", "/kyc/basic-info", kyc.submit_basic_info),
    ("POST", "/kyc/upload-id", kyc.upload_id_doc),
    ("POST", "/kyc/delete-id", kyc.delete_id_doc),
    ("POST", "/kyc/upload-face", kyc.upload_face),
    ("POST", "/kyc/face/{angle}/delete", kyc.delete_face),
    ("POST", "/kyc/reset", kyc.reset_kyc),
    ("GET", "/kyc/admin", kyc.kyc_admin_page),
    ("GET", "/kyc/admin/{user_id}", kyc.kyc_admin_detail),
    ("POST", "/kyc/admin/{user_id}/approve", kyc.approve_kyc),
    ("POST", "/kyc/admin/{user_id}/reject", kyc.reject_kyc),
    ("GET", "/kyc/uploads/{user_id}/{filename}", kyc.serve_kyc_upload),
    ("POST", "/kyc/register-stranger", kyc.register_stranger),
    # frontend/handlers/reminders.py
    ("GET", "/api/reminders/badge", reminders.get_reminders_badge),
    ("GET", "/api/reminders/all", reminders.get_all_reminders),
    ("POST", "/api/reminders", reminders.create_reminder_api),
    ("PUT", "/api/reminders/{reminder_id}", reminders.update_reminder_api),
    ("POST", "/api/reminders/{reminder_id}/complete", reminders.complete_reminder_api),
    ("POST", "/api/reminders/{reminder_id}/renew", reminders.renew_reminder_api),
    ("POST", "/api/reminders/{reminder_id}/reopen", reminders.reopen_reminder_api),
    ("DELETE", "/api/reminders/{reminder_id}", reminders.delete_reminder_api),
    ("GET", "/admin/api/reminders/badge", reminders.get_reminders_badge),
    ("GET", "/admin/api/reminders/all", reminders.get_all_reminders),
    ("POST", "/admin/api/reminders", reminders.create_reminder_api),
    ("PUT", "/admin/api/reminders/{reminder_id}", reminders.update_reminder_api),
    ("POST", "/admin/api/reminders/{reminder_id}/complete", reminders.complete_reminder_api),
    ("POST", "/admin/api/reminders/{reminder_id}/renew", reminders.renew_reminder_api),
    ("POST", "/admin/api/reminders/{reminder_id}/reopen", reminders.reopen_reminder_api),
    ("DELETE", "/admin/api/reminders/{reminder_id}", reminders.delete_reminder_api),
    # frontend/site_controller.py
    ("GET", "/admin", site_handlers.admin_root),
    ("GET", "/sw.js", site_handlers.kill_service_worker),
    ("WS", "/ws/notifications", site_handlers.websocket_endpoint),
    ("POST", "/api/internal/notify-inquiry", site_handlers.notify_inquiry),
]


def register_routes(app: FastAPI) -> None:
    for method, path, handler, *options in ROUTES:
        if method == "WS":
            app.add_api_websocket_route(path, handler)
        else:
            app.add_api_route(path, handler, methods=method.split("|"), **(options[0] if options else {}))
