# Câu trả lời mẫu cho đơn xin quyền Google Ads API (viết bằng tiếng Anh vì người duyệt đọc tiếng Anh)

Tệp thiết kế cần tải lên: `google_ads_api_design_document.pdf` (cùng thư mục, chỉ nhận .pdf / .doc / .rtf).
Số liệu trong tài liệu là mẫu; phần gọi API đang được phát triển, tài liệu ghi rõ điều đó. Nếu thông tin thật thay đổi (ngân sách, số người dùng, địa chỉ website) thì sửa cả tài liệu lẫn câu trả lời cho khớp nhau.

**4. URL website chính của công ty**
`https://app.kubenetes.store/xe-cau`
(đúng trang chủ đã xác minh thương hiệu; đừng dùng trang gốc `/`)

**5. Mô hình kinh doanh, công cụ API và đối tượng sử dụng**
```
CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH (tax code 2500774911, Phu Tho, Vietnam) is a small transport and crane-rental company. We sell services (self-propelled crane trucks, calibration test weights, freight) directly to local businesses; we do not sell advertising or software. We advertise our own services on Google Search from one Google Ads account that we own, with a monthly budget of about VND 5.3 million, and customers contact us by phone, Zalo or web form.

The API tool is an internal back-office web application that we are building for that single account. It (1) reads the performance of our own campaigns (impressions, clicks, cost, phone calls, conversions) so the owner can compare cost per call with our invoices and website leads, and (2) enables or pauses our existing Search campaigns on the planned start and end dates, so a short campaign (for example for a return trip to another province) is never left running by mistake. It does not create campaigns, change budgets, bids, keywords or ads, and it does not manage anyone else's account. Changes are in dry-run mode until the owner explicitly turns on live mode.

Audience: only our own staff (the owner and two or three employees) with individual logins and role-based permissions. There is no external or third-party access and no resale.
```

**6. Tài liệu thiết kế**
Tải lên `google_ads_api_design_document.pdf` (5 trang, khoảng 1,8 MB).

**7. Công cụ có cho người dùng ngoài tổ chức dùng không?** → **No**

**8. Có dùng token với công cụ do người khác phát triển không?** → **No**

**9. Công cụ hỗ trợ loại chiến dịch nào?**
```
Search campaigns only (including phone-call assets). The tool reads and switches the status of existing campaigns. It does not support or use Display, Video, Shopping, Performance Max, App or Demand Gen campaigns.
```
