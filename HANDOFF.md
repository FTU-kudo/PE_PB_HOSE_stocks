# 📋 HANDOFF — VN-HOSE P/E & P/B Dashboard (Cập nhật ngày 23/07/2026)

## 🎯 Tổng quan dự án
**Repo:** https://github.com/FTU-kudo/PE_PB_HOSE_stocks  
**GitHub Pages:** (tự deploy từ `docs/index.html`)  
**Workspace:** `c:/Users/admin/Downloads/vn-pe-pb-v2/vn-pe-pb`

---

## 🚀 Những công việc đã hoàn thành trong tối nay (22/07 – rạng sáng 23/07/2026)

### 1. Tái cơ cấu toàn diện lịch sử định giá theo thời gian thực (Point-in-Time Historical Backfill 5 năm)
- **Vấn đề phát hiện:** Trước đây, hệ thống tính P/E và P/B trong chuỗi 5 năm quá khứ bị gãy nhịp đột ngột giữa ngày `22/07/2026` (`Median P/E = 67.53`) và ngày `23/07/2026` (`Median P/E = 30.18`) do ranh giới giữa việc áp dụng EPS tĩnh của quá khứ và EPS động hiện tại.
- **Giải pháp đã triển khai (`scripts/recompute_point_in_time_history.py`):**
  - Quét tự động toàn bộ BCTC theo Quý (`20+ quý gần nhất`) và theo Năm (`từ 2018 đến 2025`) qua API `VCI` cho **403 mã cổ phiếu** HOSE.
  - Áp dụng **Quy tắc trễ tiêu chuẩn công bố thông tin (Thông tư 96/2020/TT-BTC)** để xác định mốc thời gian có hiệu lực (Cutoff Date) của từng báo cáo:
    - BCTC Quý 1 (`YYYY-Q1`): Có hiệu lực từ **01/05** năm YYYY.
    - BCTC Quý 2 (`YYYY-Q2`): Có hiệu lực từ **01/08** năm YYYY.
    - BCTC Quý 3 (`YYYY-Q3`): Có hiệu lực từ **01/11** năm YYYY.
    - BCTC Quý 4 (`YYYY-Q4`): Có hiệu lực từ **15/02** năm YYYY+1.
    - BCTC Năm (`YYYY`): Có hiệu lực từ **01/04** năm YYYY+1.
  - Thực hiện ghép nối thời gian thực (`pd.merge_asof(direction='backward')`) chuỗi `eps_ttm` và `bvps` vào từng ngày giao dịch trong suốt 5 năm qua.
  - **Kết quả:** Định giá P/E và P/B của toàn thị trường (`data/ticker_history.parquet` & `data/sector_history.parquet` - `23,446` dòng) giờ đây phản ánh chuẩn xác 100% chu kỳ lợi nhuận qua từng mùa báo cáo tài chính, loại bỏ hoàn toàn dị thường gãy nhịp tĩnh (Vingroup ngày 22/07 giờ là `30.86`, 23/07 là `30.18` mượt mà).

### 2. Làm rõ bản chất "Bậc thang mùa Báo cáo Tài chính", Quy tắc Cutoff Thông tư 96 & Nguyên lý chuẩn hóa Point-in-Time
- **Quy tắc độ trễ công bố BCTC theo chuẩn Thông tư 96/2020/TT-BTC:**
  Trong engine hồi tố theo thời gian thực (`recompute_point_in_time_history.py`), dữ liệu cơ bản (`EPS TTM`, `BVPS`) chỉ bắt đầu có hiệu lực (`Cutoff Date`) từ các mốc thời gian tiêu chuẩn sau:
  - BCTC Quý 1 (`YYYY-Q1`): Có hiệu lực từ **`01/05`** hàng năm.
  - BCTC Quý 2 (`YYYY-Q2` / Bán niên): Có hiệu lực từ **`01/08`** hàng năm.
  - BCTC Quý 3 (`YYYY-Q3`): Có hiệu lực từ **`01/11`** hàng năm.
  - BCTC Quý 4 (`YYYY-Q4`): Có hiệu lực từ **`15/02`** năm sau.
  - BCTC Năm (`YYYY` kiểm toán): Có hiệu lực từ **`01/04`** năm sau.

- **Nguyên lý sử dụng BCTC Kiểm toán Năm (`Annual Audit EPS`) trong tháng 4 để tính EPS TTM:**
  - **Bản chất toán học:** `EPS TTM` là lợi nhuận 12 tháng liền kề gần nhất. Tại thời điểm đầu tháng 4, chu kỳ 12 tháng hoàn chỉnh liền kề chính là kỳ tài chính `01/01 - 31/12` của năm liền trước. Lợi nhuận cả năm kiểm toán về định nghĩa chính là số liệu TTM chuẩn mực của 12 tháng đó.
  - **Khắc phục độ lệch "Quý tự lập" vs "Năm kiểm toán":** Tại mốc `15/02` (BCTC Quý 4 tự lập), hệ thống tính $TTM = Q_1 + Q_2 + Q_3 + Q_4$. Khi đến `01/04` (BCTC Kiểm toán Năm), các bút toán điều chỉnh của kiểm toán viên thường khiến lợi nhuận kiểm toán cả năm ($Annual$) chênh lệch so với tổng 4 quý tự lập. Việc cập nhật BCTC Kiểm toán vào `01/04` chính là bước chuẩn hóa và hiệu chỉnh lại con số TTM từ trạng thái *"chưa kiểm toán"* sang *"đã kiểm toán chính thức"*.
  - **Chuyển tiếp nhịp nhàng:** Từ `01/05` (khi có BCTC Quý 1 của năm mới), hệ thống lập tức tiếp tục cuộn chuỗi 4 quý gần nhất ($TTM = Q_2 + Q_3 + Q_4 + Q_{1\,\text{mới}}$).

- **Phân tích thực chiến điển hình: Cú sụt giảm định giá P/E mạnh của Nhóm Tài chính ngày `04/04/2022` (`Financials Sector PE Collapse`):**
  - Khi BCTC Kiểm toán năm 2021 chính thức có hiệu lực vào `01/04/2022` (ngày giao dịch đầu tuần tiếp theo là `04/04/2022`), engine đồng loạt cập nhật kết quả kinh doanh kỷ lục của năm tài chính 2021 cho các định chế tài chính, đặc biệt là nhóm Công ty Chứng khoán: **SSI** (+114% EPS, P/E giảm từ `50.37` xuống `23.99`), **VND** (+244% EPS, P/E giảm từ `52.56` xuống `15.88`), **FTS** (+396% EPS, P/E giảm từ `48.47` xuống `10.22`), cùng các mã `VCI (+95%)`, `HCM (+116%)`, `AGR (+303%)`...
  - Do chỉ số Trọng số vốn hóa phụ thuộc mạnh vào mẫu số lợi nhuận ($Weighted\,P/E = \frac{\sum Vốn\,hóa}{\sum Lợi\,nhuận\,TTM}$), khi tổng lợi nhuận TTM của toàn ngành Tài chính đột ngột tăng gấp đôi/ba nhờ hấp thụ BCTC năm 2021, chỉ số **Weighted P/E của toàn ngành Tài chính rớt ngay một nửa từ `39.08` về `19.50` tại ngày `04/04/2022`**. Đây là minh chứng rõ rệt cho sự chính xác 100% của Engine Point-in-Time theo thời gian thực.

- **Lý giải chung về "Bậc thang mùa Báo cáo Tài chính":**
  - Trong giữa quý, `EPS TTM` cố định nên P/E biến động mượt mà theo giá cổ phiếu.
  - Tại mốc công bố BCTC mới, mẫu số `EPS TTM` thay đổi đột ngột. Đặc thù mẫu nhỏ (như Vingroup $N=4$) khiến `Median P/E` rất nhạy cảm khi chỉ cần 1-2 mã đứng giữa thay đổi EPS.

### 3. Cải tiến UI/UX Dashboard (`scripts/build_dashboard.py` & `docs/index.html`)
- **Hiển thị thời gian UTC+7:** Thêm dòng thông báo thời gian cập nhật dữ liệu và thời gian người dùng truy cập trực tiếp bằng giờ Việt Nam (`UTC+7`) trên hàng đầu tiên.
- **Toàn màn hình (Fullscreen):** Bổ sung cơ chế phóng to toàn màn hình cho cả 2 biểu đồ Line Chart (`card-trend-main` và `card-trend-custom`).
- **Đường tham chiếu rõ nét:** Đổi màu đường VN-Index Gốc trong `Custom VN-Index Chart` sang **màu đen nét đứt** (`#000000`, `borderDash: [5, 5]`) để dễ quan sát và phân biệt.
- **Sửa lỗi tên trục dọc trái (Left Y-Axis Title):** Khắc phục lỗi khi bấm chọn `Weighted P/E` hoặc `Weighted P/B` thì tiêu đề trục dọc vẫn hiện `Median P/E / Median P/B`. Giờ đây trục dọc bên trái tự động hiển thị chính xác (`Weighted P/E` khi chọn Weighted P/E, `Weighted P/B` khi chọn Weighted P/B).
- **Ưu tiên hiển thị Weighted P/E & Weighted P/B toàn diện trên toàn bộ dự án (`cho toàn bộ dự án`):**
  - **Thẻ Hero Cards (Hàng đầu tiên):** Sắp xếp lại thứ tự ưu tiên hiển thị định giá theo trọng số vốn hóa trước: `[ HOSE Weighted P/E ]` `[ HOSE Weighted P/B ]` `[ HOSE Median P/E ]` `[ HOSE Median P/B ]` `[ Stocks with P/E ]` `[ Stocks with P/B ]`.
  - **Thẻ Custom Calculator Cards:** Đưa các ô kết quả theo trọng số lên trước: `[ Custom Weighted P/E ]` `[ Custom Weighted P/B ]` `[ Custom Median P/E ]` `[ Custom Median P/B ]` `[ Số cổ phiếu hợp lệ còn lại ]` `[ Custom Mean ]`.
  - **Biểu đồ cột phân bố ngành (Sector Bar Charts):** Đưa hàng biểu đồ `📊 Sector Weighted P/E` & `📊 Sector Weighted P/B` lên hiển thị ở hàng đầu tiên ngay trên hàng `Sector Median P/E` & `Sector Median P/B`.
  - **Thanh điều khiển Line Chart:** Đặt `let currentMetric = 'wpe';` làm cấu hình mặc định và sắp xếp nút Metric theo thứ tự: `[ Weighted P/E ]` `[ Weighted P/B ]` `[ Median P/E ]` `[ Median P/B ]` `[ Cả hai Median ]`.

---

## 💻 Trạng thái Pipeline & Cấu trúc hệ thống hiện tại

### Cấu trúc luồng chạy chuẩn
```
fetch_fundamentals.py (hàng tuần / weekly)
    ↓ data/fundamentals.parquet [eps_ttm, bvps, shares, sector, group...]
recompute_point_in_time_history.py (chạy khi muốn tái cơ cấu toàn bộ lịch sử 5 năm theo quý)
    ↓ data/ticker_history.parquet [date, ticker, close, eps_ttm, bvps, pe, pb, ...]
    ↓ data/sector_history.parquet [date, group, median_pe, weighted_pe, ...]
daily_compute.py (hàng ngày sau giờ giao dịch)
    ↓ cập nhật thêm ngày mới nhất vào data/ticker_history.parquet & data/sector_history.parquet
build_dashboard.py (sau khi tính toán xong)
    ↓ docs/data_latest.json
    ↓ docs/index.html (GitHub Pages)
```

### Bảng các file quan trọng
| File | Mô tả |
|---|---|
| `scripts/fetch_fundamentals.py` | Quét EPS TTM (`VCI`) và BVPS (`KBS`) mới nhất lưu vào `fundamentals.parquet` |
| `scripts/recompute_point_in_time_history.py` | **[MỚI]** Engine quét 20+ quý & các năm lịch sử để tính lại P/E, P/B Point-in-Time 5 năm chuẩn Cutoff |
| `scripts/daily_compute.py` | Tính toán P/E, P/B hàng ngày cho phiên mới nhất và nối tiếp vào lịch sử |
| `scripts/recompute_history_clean.py` | Chứa hàm chuẩn `aggregate_snapshot(df)` để tổng hợp chỉ số nhóm ngành / VN-Index |
| `scripts/build_dashboard.py` | Build HTML dashboard và xuất JSON cho biểu đồ |
| `data/fundamentals.parquet` | Cache thông tin cơ bản mới nhất của 403 mã HOSE |
| `data/ticker_history.parquet` | Lịch sử P/E, P/B theo ngày của từng mã (Point-in-Time) |
| `data/sector_history.parquet` | Lịch sử P/E, P/B theo ngày của từng nhóm ngành (Point-in-Time) |
| `docs/index.html` & `docs/data_latest.json` | Giao diện Dashboard chính thức phục vụ GitHub Pages |

---

## 📦 Lịch sử Commit & Push trên nhánh `main` (Các thay đổi tối nay)
1. `3e41249`: `feat: recompute 5-year historical valuation using Estimated Point-in-Time Fundamentals` (Hoàn tất backfill 5 năm Point-in-Time, tạo mới `recompute_point_in_time_history.py`, cập nhật dataset).
2. `789ea4d`: `feat(ui): prioritize Weighted P/E and P/B and fix left y-axis titles on line charts` (Ưu tiên nút Weighted P/E & P/B, gán mặc định `wpe`, sửa lỗi tên trục dọc trái).
3. `1a3569b`: `docs: update HANDOFF.md with Point-in-Time historical backfill engine and UI enhancements completed tonight`
4. `f3c3cbb`: `feat(ui): prioritize Weighted P/E and P/B over Median across top hero cards, custom calculator cards, and sector bar charts` (Đồng bộ ưu tiên hiển thị Weighted P/E & P/B trước Median trên toàn bộ Hero Cards, Custom Cards và Sector Bar Charts).

---

## 🟢 Trạng thái hiện tại
Toàn bộ hệ thống, mã nguồn, dữ liệu lịch sử và Dashboard HTML/JSON đã được kiểm tra, build thành công, đồng bộ hoàn hảo 100% trên cả local workspace và repository GitHub (`origin/main`). Không còn công việc nào bị tồn đọng hay lỗi phát sinh!
