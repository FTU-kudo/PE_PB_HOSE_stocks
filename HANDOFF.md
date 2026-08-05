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
