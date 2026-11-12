# Kế hoạch vận hành Google Ads và Facebook Page

Lập ngày 29/10/2026 sau khi kiểm tra trực tiếp mã nguồn, cơ sở dữ liệu và bảng mã địa lý chính thức của Google (bản 06/10/2026).
Mục tiêu: hai kênh chạy đều, đo được hiệu quả, không bỏ sót việc, không tốn tiền oan, không đăng trùng hay đăng nhầm.

Tài liệu đi kèm: `ke_hoach_chien_dich/ke_hoach_tong_the.md` (chiến lược, ngân sách),
`bo_tu_khoa/`, `mau_noi_dung_ad_copy/`. Phần việc code nằm ở `.ai/plans/qc-01 … qc-04` (giao Antigravity, Claude duyệt).

## 1. "Chạy mượt" gồm năm điều kiện

| # | Điều kiện | Cách kiểm tra |
|---|-----------|---------------|
| 1 | Đo được: mỗi form, cuộc gọi, lượt bấm Zalo đều biết đến từ kênh nào | Trang Yêu cầu tư vấn ghi nguồn (Google Ads / Facebook / tìm kiếm tự nhiên / trực tiếp); GTM Preview thấy `generate_lead`, `click_call`, `click_zalo` |
| 2 | Không quên: đến ngày bật hoặc tắt chiến dịch thì có tin nhắc | Tin Telegram lúc 07:30 mỗi ngày có việc |
| 3 | Không tốn oan: chiến dịch hết hạn không còn chạy, có ngưỡng dừng rõ | Mục 5; nhóm "đã hết hạn hôm qua" trong tin nhắc được xác nhận đã tắt |
| 4 | Không đăng trùng, không đăng nhầm: mỗi khung giờ đúng một bản nháp, bấm duyệt hai lần vẫn một bài | Test tự động; trạng thái "Đang gửi lên Facebook" |
| 5 | Tự báo khi hỏng: token Facebook sắp hết hạn, Graph lỗi, AI không viết được bài, thiếu cấu hình | Bảng "Sẵn sàng chạy" và tin Telegram |

## 2. Hiện trạng đã kiểm tra (29/10/2026)

| Hạng mục | Hiện trạng | Hậu quả |
|----------|-----------|---------|
| Theo dõi chuyển đổi | Chưa nhập GTM_ID, FB_PIXEL_ID. Dù nhập cũng không hiện lên trang, vì `get_landing_settings()` (`frontend/routers/cms_router.py:71`) chỉ đọc khóa LANDING_*. Layout trang công khai chính và trang tĩnh không có mã GTM/Pixel (chỉ trang xe cẩu có) | Không đo được gì; không dùng được Smart Bidding |
| Form tư vấn | Chỉ lưu tên, SĐT, nhu cầu, trang; không lưu gclid/utm, không bắn sự kiện. Từ trước đến nay chỉ có 1 yêu cầu | Không biết khách đến từ quảng cáo nào |
| Hotline | Chưa có khóa LANDING_PHONE / LANDING_ZALO; trang xe cẩu dùng 0919497568 (`frontend/crane/config.py`), trang tĩnh dùng 0912559868 | Quảng cáo dẫn khách đến hai số khác nhau; cần chủ chọn |
| Google Ads | Chưa nhập OAuth (client secret, refresh token) và customer id; 0 chiến dịch. Module chỉ là sổ lịch cục bộ: không bật/tắt thật, số liệu không tự cập nhật, trạng thái chỉ tính lúc tạo | Dễ quên bật/tắt; số liệu nhập tay |
| Mã địa lý | 23/23 mã tỉnh trong module không khớp bảng chính thức của Google; Hải Phòng và Hà Tĩnh trùng mã 20700. Từ 7/2025 cả nước còn 34 tỉnh/thành, 29 tỉnh cũ đã sáp nhập | Xuất/nhập chiến dịch nhắm sai vùng |
| Cấu hình API trên giao diện | Không có chỗ nhập client id / secret / refresh token; developer token đang in nguyên văn vào HTML của trang | Không kết nối API được; lộ bí mật qua mã nguồn trang |
| Facebook | Page và token đã có (token Page không hết hạn; quyền truy cập dữ liệu hết hạn 27/01/2027). Tự động đăng đang tắt, chưa có chủ đề, chưa có bản nháp | Chưa chạy |
| Độ bền Facebook | Lỗi Graph chưa phân loại; bấm duyệt hai lần có thể đăng hai bài; duyệt sát giờ làm hệ thống sinh thêm bản nháp cho cùng khung giờ; lỗi mạng làm lộ token vào thông báo lỗi lưu trong DB; không cảnh báo token | Đăng trùng, tốn lượt AI, hỏng mà không biết |

## 3. Lộ trình

Việc của chủ (giai đoạn 0) chạy song song với code (giai đoạn 1–3). Mỗi plan code được duyệt xong mới tính là hoàn thành.

### Giai đoạn 0 — việc của chủ, không cần code

| Việc | Chi tiết | Khi nào |
|------|----------|---------|
| 0.1 Chọn MỘT số hotline và MỘT số Zalo cho quảng cáo | Hiện có 0919497568 và 0912559868. Chọn xong nhập `LANDING_PHONE`, `LANDING_ZALO` (thêm `LANDING_PHONE_WAREHOUSE`, `LANDING_ZALO_WAREHOUSE` nếu trang nhà xưởng dùng số riêng) tại Quản lý API Key | Trước khi bật quảng cáo |
| 0.2 Tạo container Google Tag Manager | Lấy mã `GTM-XXXXXXX`, nhập khóa `GTM_ID` tại Quản lý API Key (sau plan qc-01 có sẵn dòng gợi ý) | Sau qc-01 |
| 0.3 Quyết định nơi đặt Meta Pixel | Chỉ MỘT nơi: đặt trong GTM (khuyên dùng, để trống `FB_PIXEL_ID`) hoặc nhập `FB_PIXEL_ID` để gắn trực tiếp. Đặt cả hai sẽ đếm đôi; bảng "Sẵn sàng chạy" cảnh báo | Sau qc-01 |
| 0.4 Gắn thẻ trong GTM | Trigger "Custom Event" cho `generate_lead`, `click_call`, `click_zalo`. Thẻ: Google Ads Conversion (mỗi sự kiện một hành động chuyển đổi), GA4 Event, Meta Pixel (`Lead` cho form, `Contact` cho gọi/Zalo). Dùng Preview của GTM để thử | Sau 0.2 |
| 0.5 Tạo hành động chuyển đổi trong Google Ads | "Cuộc gọi từ quảng cáo" (thời lượng tối thiểu 30 giây) và "Gửi form" đặt Chính (Primary); "Bấm gọi", "Bấm Zalo" đặt Phụ (Secondary) lúc đầu. Liên kết GA4. Bật gắn thẻ tự động (auto-tagging) | Trước khi chi tiền |
| 0.6 Mẫu theo dõi URL | Cấp tài khoản Google Ads: `{lpurl}?utm_source=google&utm_medium=cpc&utm_campaign={campaignid}&utm_term={keyword}`. Quảng cáo Facebook (nếu chạy): `utm_source=facebook&utm_medium=paid_social&utm_campaign=<tên chiến dịch>` | Trước khi chi tiền |
| 0.7 Mở API Google Ads | Từ 09/09/2026 Google đã bỏ developer token; quyền gắn với dự án Google Cloud (số 686766750544). Bật Google Ads API, xin mức truy cập Explorer tại https://console.cloud.google.com/google/ads-apis/overview (duyệt tự động), lấy customer id và refresh token OAuth, nhập tại Quảng cáo ▸ Cấu hình API | Bắt đầu sớm; chỉ chặn plan qc-04 |
| 0.8 Facebook | Nhập 5–8 chủ đề và ghi chú thương hiệu (dịch vụ, hotline, giọng văn) ở tab "Tự động"; CHƯA bật cho đến khi qua mục 7. Đặt lịch gia hạn token trước 27/01/2027 | Sau qc-03 |

**Cách theo dõi đang dùng từ 29/10/2026: Google tag cài thẳng (không cần GTM).** Mã `AW-18542199918` của tài khoản Google Ads đã được cài vào mọi trang công khai (khóa `GADS_TAG_ID` trong Quản lý API Key). Muốn Google Ads tính lượt gửi form, bấm gọi, bấm Zalo là chuyển đổi, mỗi sự kiện cần một nhãn:
1. Trong Google Ads: Mục tiêu ▸ Chuyển đổi ▸ Tạo chuyển đổi ▸ Website, tạo 3 hành động ("Gửi form", "Bấm gọi", "Bấm Zalo"). Ở bước cài đặt, chọn dùng đoạn mã sự kiện: đoạn mã có dạng `gtag('event', 'conversion', {'send_to': 'AW-18542199918/NHÃN'})`.
2. Chép phần NHÃN (sau dấu `/`) vào Quản lý API Key: `GADS_LABEL_LEAD` (form), `GADS_LABEL_CALL` (bấm gọi), `GADS_LABEL_ZALO` (bấm Zalo). Chờ tối đa 60 giây là website bắt đầu gửi chuyển đổi.
3. Để trống `GTM_ID` (có cả hai thì bảng "Sẵn sàng chạy" cảnh báo vì dễ đếm đôi). Cuộc gọi từ tiện ích cuộc gọi của quảng cáo do Google tự đo, không cần nhãn.
Cách dùng Google Tag Manager ở bảng trên vẫn dùng được nếu sau này muốn quản lý nhiều thẻ.

Quy ước sự kiện (dataLayer): `generate_lead` (form gửi thành công; kèm `source_page`, `inquiry_id`, `event_id`), `click_call` (bấm số điện thoại), `click_zalo` (bấm Zalo).
Bài đăng Page: hệ thống tự thêm `utm_source=facebook&utm_medium=social&utm_campaign=fb_post_<mã bài>` vào liên kết về website của công ty.

### Giai đoạn 1–3 — code (Antigravity)

| Plan | Nội dung | Phụ thuộc | Cần chủ cấp |
|------|----------|-----------|-------------|
| qc-01 | Sửa lỗi GTM/Pixel không hiện; mã theo dõi ở mọi trang công khai; form lưu gclid/utm/nguồn; bắn `generate_lead`, `click_call`, `click_zalo`; trang Yêu cầu tư vấn hiện kênh | Không | `GTM_ID` để thấy tác dụng |
| qc-02 | Mã địa lý đúng (34 tỉnh + 29 tỉnh cũ); tự chuyển trạng thái chiến dịch theo ngày; tin Telegram nhắc bật/tắt 07:30; nhập số liệu tay + chi phí/cuộc gọi; cấu hình API đầy đủ và không lộ bí mật; bảng "Sẵn sàng chạy" | Không (tự bỏ qua mục chưa có từ qc-01/qc-03) | Không |
| qc-03 | Facebook: phân loại lỗi Graph, thử lại an toàn, che token, duyệt nguyên tử (không đăng trùng), một bản nháp mỗi khung giờ, cảnh báo Telegram, kiểm tra sức khỏe token, tự thêm UTM | Không | Không |
| qc-04 | Google Ads API: kiểm tra kết nối, kéo số liệu, đồng bộ bật/tắt; mặc định CHẠY THỬ, chỉ thật khi chủ bật công tắc | qc-02 | Mức truy cập Explorer của dự án Cloud, refresh token OAuth, customer id |

Tiến độ (cập nhật 29/10/2026 lúc 20:00): qc-01, qc-02, qc-03 đã làm xong, Claude đã duyệt từng diff và chạy lại toàn bộ test; qc-04 chưa chạy vì chủ chưa có refresh token OAuth và chưa xin mức truy cập Explorer (plan đã viết sẵn tại `.ai/plans/qc-04-google-ads-api.md`).
Khác với bản plan: bảng "Sẵn sàng chạy" chỉ yêu cầu 5 khóa Google Ads (ID tài khoản quản lý `login_customer_id` là tùy chọn); cột nguồn khách của bảng `inquiries` đã được tạo trên DB thật.

Ghi chú phối hợp: các mục B5, B6, C5 trong `.ai/plans/00-ke-hoach-tong-the.md` (sự kiện chuyển đổi, cột nguồn cho `inquiries`, chiến dịch/Facebook/form tư vấn) trùng phạm vi qc-01 … qc-03; phần đó làm ở đây, không làm lại ở kế hoạch kia.

### Giai đoạn sau (khi đã có dữ liệu thật)
- Gợi ý chiến dịch "chuyến về" tự động từ chuyến xe sắp chạy trong menu Vận chuyển (tỉnh đến + ngày đi → đề xuất bật chiến dịch cho tỉnh đó trước 2–3 ngày).
- Nhập chuyển đổi ngoại tuyến: gclid đã lưu cùng yêu cầu tư vấn; khi chốt đơn (có hóa đơn) tải lên Google để Smart Bidding học theo doanh thu thật.
- Lấy số liệu bài đăng Facebook (tiếp cận, tương tác) để chọn chủ đề hiệu quả.

## 4. Nhịp việc

Hằng ngày (khoảng 5 phút, buổi sáng):
- Đọc tin nhắc Telegram 07:30: chiến dịch cần bật, tắt hôm nay; chiến dịch hết hạn hôm qua phải xác nhận đã tắt trên Google Ads.
- Trang Yêu cầu tư vấn: gọi lại khách chưa đọc (website cam kết liên hệ trong 30 phút).
- Facebook: duyệt bản nháp ngay khi có tin "Bài Facebook mới chờ duyệt". Facebook chỉ nhận hẹn giờ cách giờ đăng ít nhất 10 phút; duyệt muộn hơn thì bài đăng ngay.
- Chưa có API: ghi chi tiêu, số cuộc gọi hôm qua từ Google Ads vào nút "Cập nhật số liệu".

Hằng tuần (20–30 phút, sáng thứ Hai):
- Google Ads: xem báo cáo "Cụm từ tìm kiếm" 7 ngày; từ khóa rác thêm vào danh sách phủ định (`bo_tu_khoa/tu_khoa_phu_dinh.txt`); xem giờ/ngày nào có cuộc gọi.
- Chuyến xe tuần tới (menu Vận chuyển): tạo chiến dịch "chuyến về" cho tỉnh đến, bật trước 2–3 ngày theo kế hoạch tổng thể.
- Facebook: xem bài nào có bình luận/tin nhắn nhiều, chỉnh chủ đề; trả lời tin nhắn của Page.

Hằng tháng:
- Đối chiếu chi tiêu thực tế với ngân sách (khoảng 5,3 triệu đồng/tháng theo kế hoạch tổng thể), chi phí mỗi cuộc gọi/form, và cơ cấu nguồn khách trong Yêu cầu tư vấn.
- Facebook: còn dưới 30 ngày trước hạn token thì gia hạn (xem ghi chú truy cập Facebook Page). Google Ads: kiểm tra phiên bản API còn được hỗ trợ (v25 dự kiến ngừng 08/2027).

## 5. Quy tắc tối ưu và dừng chi (điểm khởi đầu, chỉnh theo dữ liệu thật sau 30 ngày)

- Hai tuần đầu dùng "Tối đa hóa số lượt nhấp" kèm CPC tối đa; chuyển sang "Tối đa hóa lượt chuyển đổi" khi có khoảng 30 chuyển đổi; chỉ đặt CPA mục tiêu khi 30 ngày liền ổn định, lấy CPA trung bình × 1,1.
- Đổi ngân sách tối đa ±20% mỗi lần, cách nhau ít nhất 3 ngày; không đổi chiến lược thầu và từ khóa trong cùng một lần.
- Từ khóa hoặc nhóm chi từ 3 lần ngân sách ngày trở lên trong 7 ngày mà không có chuyển đổi: tạm dừng và xem lại cụm từ tìm kiếm.
- Chiến dịch "chuyến về": tắt ngay khi hết ngày kết thúc; không để chạy sang ngày sau.
- Giờ chạy theo kế hoạch tổng thể (06:00–21:00); thu hẹp theo báo cáo giờ sau 14 ngày.
- Không cam kết giá, thời gian hay chất lượng mà hệ thống chưa có số liệu (AI viết bài đã bị cấm bịa giá, số liệu).

## 6. Rủi ro và cách xử lý

| Rủi ro | Cách xử lý |
|--------|-----------|
| Đếm chuyển đổi hai lần (Pixel đặt cả trong GTM lẫn trực tiếp) | Bảng "Sẵn sàng chạy" cảnh báo; chọn một nơi theo mục 0.3 |
| Quảng cáo dẫn khách đến hai số điện thoại khác nhau | Chốt số ở mục 0.1 trước khi bật |
| Quên tắt chiến dịch hết hạn | Tin nhắc 07:30; có API thì hệ thống tự tắt (qc-04) |
| Token Facebook/Google hết hạn lúc không ai biết | Kiểm tra sức khỏe mỗi 6 giờ, cảnh báo Telegram khi còn ≤ 14 ngày hoặc đã hỏng |
| Lộ bí mật qua giao diện hoặc thông báo lỗi | Trường bí mật chỉ hiện "Đã lưu (…abcd)"; thông báo lỗi tự che token |
| Google đổi/ngừng phiên bản API | Phiên bản cấu hình được (`google_ads_api_version`); lỗi 404 theo phiên bản có gợi ý đổi |
| Hệ thống tự bật/tắt nhầm chiến dịch thật | Mặc định chạy thử; chỉ bật thật khi chủ xác nhận, tối đa 10 thay đổi mỗi lần, không bao giờ bật chiến dịch đã hết hạn, mọi thay đổi ghi nhật ký |
| Backend khởi động lại lúc sửa code làm cầu Zalo ngắt vài giây | Plan yêu cầu ghi file backend một lần hoàn chỉnh; tin nhắn Zalo tới đúng lúc đó có thể bỏ lỡ nên tránh giờ cao điểm đặt đơn |

## 7. Danh sách kiểm tra trước khi chi tiền thật

- [ ] Bảng "Sẵn sàng chạy" ở trang Quảng cáo không còn mục đỏ.
- [ ] Gửi thử một form trên trang xe cẩu từ đường dẫn có `?gclid=test&utm_source=google&utm_medium=cpc`: Yêu cầu tư vấn hiện kênh "Google Ads"; GTM Preview thấy `generate_lead`.
- [ ] Bấm thử số điện thoại và nút Zalo: thấy `click_call`, `click_zalo`; hành động chuyển đổi trong Google Ads chuyển sang trạng thái ghi nhận.
- [ ] Hotline và Zalo thống nhất trên mọi trang đích.
- [ ] Tạo một chiến dịch thử với ngày bắt đầu ngày mai: sáng hôm sau nhận tin nhắc 07:30.
- [ ] Facebook: bấm "Kiểm tra kết nối" ra màu xanh; AI viết một bản nháp thử; duyệt một bài hẹn giờ thật (xóa sau khi kiểm tra); bấm duyệt hai lần liên tiếp chỉ ra một bài; nhận tin "Bài Facebook mới chờ duyệt".
- [ ] Ngân sách ngày, phương thức thanh toán và cảnh báo ngân sách trong Google Ads đã thiết lập.
- [ ] (Khi có API) chế độ "Chạy thử" chạy ít nhất 3 ngày, nhật ký đồng bộ khớp việc đã làm tay, rồi mới bật chế độ tự động.
