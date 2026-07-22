# 📋 HANDOFF — VN-HOSE P/E & P/B Dashboard (2026-07-22)

## 🎯 Tổng quan dự án
**Repo:** https://github.com/FTU-kudo/PE_PB_HOSE_stocks  
**GitHub Pages:** (tự deploy từ `docs/index.html`)  
**Workspace:** `c:/Users/admin/Downloads/vn-pe-pb-v2/vn-pe-pb`

---

## ✅ Những gì đã hoàn thành hôm nay (22/07/2026)

### 1. UI/UX Dashboard (đã commit & push)
- Làm nổi bật chỉ số HOSE trên hàng đầu tiên
- Bỏ hàng "Vingroup Ecosystem" khỏi top summary
- Đổi tiêu đề thành `📊 VN-HOSE P/E & P/B 🔍`
- Xóa dòng "As of..." ở đầu và thay footer bằng `© Bản quyền thuộc về FTU-kudo`
- Thêm **HOSE Weighted P/E** và **HOSE Weighted P/B** vào hàng đầu tiên
- Canh 5 ô Custom Calculator thành 1 hàng ngang
- Tô màu vàng nhạt cho P/E cards, hồng nhạt cho P/B cards
- Thêm **Sector Weighted P/E** và **Sector Weighted P/B** song hành cạnh Median P/E và P/B
- Ghép cặp `Sector Median P/E ↔ P/B` và `Sector Weighted P/E ↔ P/B` ngang hàng
- Đồng bộ thứ tự ngành (y-axis) giữa P/E và P/B cùng hàng
- Fix Chart.js infinite vertical resize loop bằng `position:relative; height:380px; width:100%` wrapper div
- Fix hiển thị đầy đủ 16 nhóm ngành bằng `autoSkip: false` + `maintainAspectRatio: false`

### 2. Phát hiện và phân tích vấn đề TTM EPS (CHƯA PUSH)
- Phát hiện P/E dashboard (dùng Annual EPS 2025) chênh lệch với CafeF/Vietstock
  - VIX: Dashboard = **3.38**, CafeF = **~7.11**
- Xác nhận **KBS `trailing_eps`** bị stale (chưa cập nhật BCTC Q2/2026 thực của VIX)
- Tìm ra: **VCI `income_statement(period='quarter')`** có dữ liệu Q2/2026 thực tế (lợi nhuận Q2/2026 = 75.9 tỷ)
- Tính TTM EPS thủ công từ VCI: Q3/25 + Q4/25 + Q1/26 + Q2/26 = 3,950 tỷ → EPS = 1,612đ → **P/E = 7.60** ≈ CafeF ✅

### 3. Code thay đổi cho TTM EPS (ĐÃ CODE, CHƯA PUSH — đang fetch)
**Các file đã sửa:**
- `scripts/fetch_fundamentals.py` — **Thay đổi lớn:**
  - Thêm `_extract_bvps()` (chỉ lấy BVPS từ KBS annual)
  - Thêm `_compute_ttm_eps()` — VCI quarterly `isa22` × 4 quý, fallback KBS `trailing_eps`
  - `eps_annual` → `eps_ttm` toàn bộ
- `scripts/daily_compute.py` — rename 11 chỗ `eps_annual` → `eps_ttm`
- `scripts/backfill_5y_history.py` — rename 7 chỗ
- `scripts/recompute_history_clean.py` — rename 7 chỗ
- `scripts/build_dashboard.py` — rename 11 chỗ (kể cả JS)

---

## 🔄 Đang chạy (khi tắt máy / ngày mai tiếp tục)

### `fetch_fundamentals.py` đang fetch 403 tickers
- **Bắt đầu:** 18:09 ngày 22/07/2026
- **Trạng thái:** Đang chạy (~8–10 phút tổng)
- **Log:** Đến 18:16 đã fetch được ~25-30 tickers đầu tiên
- **Nếu bị ngắt:** Cần chạy lại `python scripts/fetch_fundamentals.py`

---

## 📋 Việc cần làm ngày mai (THEO THỨ TỰ)

### Bước 1 — Kiểm tra kết quả fetch
```bash
cd c:/Users/admin/Downloads/vn-pe-pb-v2/vn-pe-pb
python -c "
import sys, pandas as pd
sys.stdout.reconfigure(encoding='utf-8')
df = pd.read_parquet('data/fundamentals.parquet')
print('Columns:', df.columns.tolist())
print('VIX:', df[df['ticker']=='VIX'][['ticker','eps_ttm','bvps','shares']].to_string())
print('Total eps_ttm valid:', df['eps_ttm'].notna().sum(), '/', len(df))
"
```
> **Kỳ vọng:** VIX `eps_ttm` ≈ 1,612 (không phải 3,619)

### Bước 2 — Nếu fetch thành công: Chạy daily_compute
```bash
python scripts/daily_compute.py
```

### Bước 3 — Rebuild dashboard
```bash
python scripts/build_dashboard.py
```

### Bước 4 — Verify P/E VIX
```bash
python -c "
import sys, pandas as pd, json
sys.stdout.reconfigure(encoding='utf-8')
data = json.load(open('docs/data_latest.json', encoding='utf-8'))
vix = [t for t in data['tickers'] if t['ticker']=='VIX']
print('VIX:', vix)
print('HOSE Median P/E:', data['market']['median_pe'])
print('HOSE Weighted P/E:', data['market']['weighted_pe'])
"
```
> **Kỳ vọng:** VIX P/E ≈ 7–8

### Bước 5 — Commit & push
```bash
git add .
git commit -m "feat: switch to TTM EPS from VCI quarterly income statement for more accurate P/E"
git push origin main
```

---

## 🔑 Kiến thức kỹ thuật quan trọng

### Tại sao VCI thay vì KBS cho TTM EPS?
- KBS `trailing_eps` có **độ trễ** sau khi BCTC mới được công bố (1–2 tuần)
- VCI `income_statement(period='quarter')` cập nhật **nhanh hơn** (vài ngày)
- VCI trả về **lợi nhuận standalone từng quý** (đã deaccumulate từ VAS YTD cumulative)
- → TTM = `isa22[Q_t] + isa22[Q_t-1] + isa22[Q_t-2] + isa22[Q_t-3]` / shares

### Tại sao BVPS vẫn dùng KBS?
- Bảng cân đối kế toán ít biến động theo quý hơn P&L
- KBS annual BVPS đủ chính xác cho P/B

### VAS Cumulative Quarterly Issue
- VAS báo cáo theo dạng **YTD cumulative** (Q2 = lợi nhuận H1, không phải Q2 riêng)
- VCI đã **tự deaccumulate** trước khi trả về API → không cần xử lý thêm
- KBS đôi khi trả về cột bị duplicate (ví dụ `2025-Q4` và `2025-Q4_1`) → tránh dùng cho TTM

### Cấu trúc dữ liệu pipeline
```
fetch_fundamentals.py (weekly)
    ↓ data/fundamentals.parquet [eps_ttm, bvps, shares, sector, group...]
daily_compute.py (daily after market close)
    ↓ data/ticker_history.parquet [date, ticker, close, pe, pb, ...]
    ↓ data/sector_history.parquet [date, group, median_pe, weighted_pe, ...]
build_dashboard.py
    ↓ docs/data_latest.json
    ↓ docs/index.html (GitHub Pages)
```

---

## 📁 Các file quan trọng
| File | Mô tả |
|---|---|
| `scripts/fetch_fundamentals.py` | Fetch BVPS (KBS) + TTM EPS (VCI primary, KBS fallback) |
| `scripts/daily_compute.py` | Tính P/E, P/B daily từ fundamentals + giá đóng cửa |
| `scripts/build_dashboard.py` | Build HTML dashboard + JSON |
| `scripts/config.py` | Các hằng số cấu hình |
| `data/fundamentals.parquet` | Cache EPS/BVPS (refresh hàng tuần) |
| `data/ticker_history.parquet` | Lịch sử P/E, P/B theo ngày từng mã |
| `data/sector_history.parquet` | Lịch sử P/E, P/B theo ngày từng nhóm ngành |
| `docs/index.html` | Dashboard HTML (GitHub Pages) |
| `docs/data_latest.json` | Dữ liệu JSON mới nhất cho dashboard |

---

## ⚠️ Lưu ý quan trọng

### Nếu fetch_fundamentals bị fail giữa chừng
- Kiểm tra xem `data/fundamentals.parquet` có cột `eps_ttm` chưa:
  ```python
  df = pd.read_parquet('data/fundamentals.parquet')
  print(df.columns.tolist())
  ```
- Nếu vẫn còn cột `eps_annual` (file cũ chưa bị ghi đè) → chạy lại `fetch_fundamentals.py`
- Nếu có cột `eps_ttm` → fetch thành công, tiếp tục bước 2

### Nếu VIX P/E vẫn sai sau khi rebuild
- Kiểm tra `eps_ttm` trong `fundamentals.parquet`
- VCI có thể chưa cập nhật Q2/2026 với một số tickers → check log để thấy tickers nào dùng KBS fallback

### Các commit đã push hôm nay
1. `feat: add color-coded PE/PB card styles, align custom calculator layout, and add sector weighted PE/PB bar charts`
2. `feat: pair median/weighted sector charts horizontally and synchronize exact y-axis sector orderings between PE and PB charts`
3. `fix: disable Chart.js y-axis tick autoSkip and set 380px min-height...`
4. `fix: wrap sector chart canvases in fixed height container div...`

### Commit cần push ngày mai
5. `feat: switch to TTM EPS from VCI quarterly income statement for more accurate P/E`
