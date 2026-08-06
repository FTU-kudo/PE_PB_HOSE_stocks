# Quá trình Xử lý & Tối ưu hóa Dự án (23/07/2026)

Tài liệu này tóm tắt toàn bộ các vấn đề đã được phát hiện và xử lý trong hệ thống tính toán P/E và P/B của sàn HOSE, bao gồm lỗi dữ liệu định giá, tối ưu hóa hiệu suất Github Actions và phân tích bất thường của biểu đồ.

## 1. Xử lý lỗi hệ thống & Tối ưu code
- **Lỗi cú pháp (Syntax Error):** Phát hiện và phân tích lỗi `unmatched ')'` trong `daily_compute.py` (do một commit cũ gây ra và đã được sửa ở commit sau đó).
- **Đồng bộ hóa tài liệu:** Cập nhật đồng loạt các từ khóa cũ `eps_annual` thành `eps_ttm` trong `README.md` để khớp với logic tính toán mới.
- **Tối ưu tốc độ Github Actions (Multithreading):** Tiến trình `fetch_fundamentals.py` cũ chạy tuần tự từng mã và mất tới hơn 1 tiếng để thu thập đủ 403 mã HOSE. Đã nâng cấp code sử dụng `concurrent.futures.ThreadPoolExecutor(max_workers=5)`, giúp giảm thời gian chạy xuống chỉ còn khoảng **10 - 15 phút**.

## 2. Sửa lỗi nghiêm trọng: Mức Weighted P/B sụt giảm phi lý (xuống 0.39)
- **Nguyên nhân:** Khám phá ra rằng script `recompute_point_in_time_history.py` đã sử dụng mã chỉ tiêu tài chính `bsa53` từ thư viện `vnstock` nguồn VCI để lấy Giá trị Sổ sách (BVPS). Tuy nhiên, đối với nhóm Ngân hàng, `bsa53` thực chất lại là **Tổng Tài Sản (Total Assets)**, vốn lớn gấp 10-15 lần Vốn Chủ Sở Hữu (`bsa78`). Điều này khiến Mẫu số (Total Book Value) của toàn bộ thị trường bị phình to ảo, kéo tụt HOSE Weighted P/B xuống mức 0.39.
- **Cách khắc phục:** 
  - Đổi mã lấy dữ liệu BVPS từ `bsa53` sang `bsa78` trong `recompute_point_in_time_history.py`.
  - Chạy script `recompute_history_clean.py` để làm sạch và lấp đầy toàn bộ lịch sử bằng dữ liệu BVPS tĩnh chuẩn xác.
  - Chạy `build_dashboard.py` để kết xuất lại Dashboard. Mức HOSE Weighted P/B đã phục hồi hoàn toàn về mức hợp lý là **1.75**.

## 3. Phân tích dị thường dữ liệu: Cú giật P/E của nhóm Dầu khí
- **Hiện tượng:** Biểu đồ 5 năm xuất hiện một gai nhọn thẳng đứng kéo P/E nhóm Dầu khí vọt lên 18.61 rồi rơi tự do về 7.71 vào giữa tháng 1/2025.
- **Nguyên nhân cốt lõi (Composition Bias / Survivorship Bias):** 
  - Nhóm Dầu khí trên HOSE bị chi phối mạnh mẽ bởi cổ phiếu BSR (Lọc Hóa Dầu Bình Sơn - vốn hóa ~67 nghìn tỷ, P/E ~5.1).
  - Từ ngày **07/01/2025 đến 16/01/2025**, BSR bị ngừng giao dịch tạm thời để chuyển sàn từ UPCOM sang HOSE.
  - Trong đúng 10 ngày này, BSR hoàn toàn biến mất khỏi dữ liệu đóng cửa hàng ngày. Do đó, rổ Dầu khí HOSE chỉ còn lại PLX (P/E ~27) và PVD (P/E ~6), khiến Weighted P/E của nhóm bị vọt lên mức ~19.
  - Tới ngày 17/01/2025, BSR chào sàn HOSE và được cập nhật lại vào rổ, lập tức kéo P/E trung bình về mức bình thường là 7.7.
  - Trên biểu đồ 5 năm, khoảng thời gian 10 ngày này bị nén lại về mặt thị giác, tạo ra ảo giác đây chỉ là một biến động trong 1-2 ngày. Dữ liệu lịch sử gốc vẫn hoàn toàn chính xác theo đúng sự thật thị trường lúc bấy giờ.

## 4. Khắc phục lỗi API: Không lấy được dữ liệu EPS (05/08/2026 12:07)
- **Hiện tượng:** Luồng Github Actions chạy nhưng báo `TTM EPS fetched: 0` đối với toàn bộ ~400 mã cổ phiếu. Dashboard kết xuất ra không hiển thị bất kỳ số liệu P/E, P/B nào (đều bằng 0 hoặc rỗng).
- **Nguyên nhân cốt lõi:** Thư viện `vnstock` vừa cập nhật lên phiên bản 4.0+. Cấu trúc dữ liệu API trả về từ lệnh `Fundamental().equity(ticker).ratio(lang='en')` bị thay đổi hoàn toàn:
  - Ở bản cũ: Trả về dạng cột đứng (mỗi cột là một chỉ số như `trailing_eps`, `book_value_per_share`, các hàng là các kỳ báo cáo).
  - Ở bản mới: Trả về dạng bảng ngang bị chuyển vị (transposed), trong đó mỗi hàng là một chỉ tiêu (`item_id`) và mỗi cột là một kỳ báo cáo (`2026-Q2`, `2025-Q4`...). Do code cũ vẫn tìm kiếm tên cột thay vì lọc theo tên hàng nên hoàn toàn không trích xuất được dữ liệu.
- **Cách khắc phục:** Cập nhật lại hàm `_extract_ttm` trong file `scripts/fetch_fundamentals.py` để hỗ trợ format mới. Bổ sung logic tự động nhận diện cấu trúc mới qua cột `item_id`, sau đó tự động tìm cột đại diện cho kỳ báo cáo gần nhất (bằng cách loại bỏ `item`/`item_id` ra khỏi danh sách cột và sắp xếp giảm dần), rồi mới trích xuất các giá trị tương ứng tại hàng `trailing_eps` và `book_value_per_share_bvps`.

## 2026-08-05 17:56:55
- Đã khắc phục triệt để lỗi 'Unknown' cho các nhóm ngành bằng cách cập nhật script etch_fundamentals.py.
- Sửa logic xử lý fallback để khi hàm VCI list_by_industry trên Github Actions bị lỗi (do thiếu hosting_service), hệ thống sẽ dự phòng sang KBS sectors và trích xuất đúng cột industry_name thay vì bị ghi đè thành 'Unknown'.
- Sửa emoji tiêu đề trong uild_dashboard.py thành 📊.
- Xử lý mượt mà conflict khi push code bằng cách merge dữ liệu remote mới nhất, sau đó chạy lại script vá lỗi trên chính dữ liệu mới đó.
- Đã xác nhận script hoạt động hoàn hảo trên Github Actions.
