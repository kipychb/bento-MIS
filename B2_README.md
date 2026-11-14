# B2 庫存、效期、盤點與報廢模組

獨立 Python 3 + SQLite 模組，不需第三方套件。主專案尚未決定技術架構，先避免更動其他組員程式。

## 單位管理（新版）
- 進貨輸入單位可選 kg、g、L、mL、顆、份、包。
- 同一品項的固定庫存單位須一致，已存在的品項批次不允許混用。
- g/kg 與 mL/L 會自動換算；輸入「包」且固定庫存單位不是「包」時，需填每包規格與規格單位。
- 例如 5 包 × 200 g，以 kg 記庫存，會存成 1 kg；如每包 30 元，將換算為每 kg 150 元。
- 若整包作為獨立消耗單位，可直接以「包」管理，無須填每包含量。
- 共用資料庫有 B1 品項主檔時，入庫單位必須跟主檔一致（原料一律 g、便當一律 份）；例如輸入 2 kg 生米會換算成 2000 g 入庫。
- 已有批次在同一品項必須有相同的固定單位；如資料庫已有不一致歷史資料，需要先清理。

## 已實作
- 原料及便當成品的分批庫存（批次編號唯一、有效期限、單價、庫存數量）
- 即將到期及已逾期提醒（僅顯示仍有庫存的批次）
- 盤點差異記錄與庫存調整
- 報廢扣庫、原因、操作人與報廢金額彙總
- 庫存異動日誌；資料庫交易保護

## 一個人就能啟動的網頁版

需要 Python 3（建議 3.10+），不需另外安裝套件。於專案根目錄執行：

```bash
python b2_app.py
```

瀏覽器開啟：http://127.0.0.1:8001（A1 用 8000；如需改埠可設環境變數 `B2_PORT`）

頁面提供庫存查詢、新增批次、效期提醒、盤點、報廢與異動紀錄。所有資料儲存於專案根目錄的共用資料庫 `bento.db`（與 A2、B1、C1、C2 同一個檔案，可用環境變數 `BENTO_DB` 改路徑）；重新啟動仍會保留。初次使用請先到「新增批次」建立資料。僅供本機演示與開發使用，不含登入權限及正式部署所需安全機制。

## 測試
在儲存庫根目錄執行：
```bash
python -m unittest discover -s tests -v
```

## 基本使用
```python
from b2_inventory import InventoryService
s = InventoryService("bento.db")
batch_id = s.receive_batch("ingredient", "rice", "RICE-001", "2026-11-01", 20, "kg", 50, "A2")
print(s.expiry_alerts(days=14))
print(s.stocktake(batch_id, 19, "B2"))
print(s.waste(batch_id, 2, "受潮", "B2"))
print(s.waste_report())
s.close()
```

## 出庫與效期（給 B1、C1 使用）

- `plan_consumption(item_type, item_id, qty, now=None)`：依 **FEFO**（先到期先出）規劃要扣哪些批次，已過期的不出；不足時丟出錯誤，不改資料。
- `apply_consumption(plan, reason, operator)`：執行上面的規劃，不自己 commit，讓 B1 生產、C1 結帳可以把扣庫存和自己的紀錄放在同一個交易。
- `consume(...)`：單一品項直接出庫。`available(item_type, item_id)`：目前可用（未過期）數量。
- 效期可以是日期 `YYYY-MM-DD`（當天結束前可用），也可以是時間 `YYYY-MM-DDTHH:MM:SS`（便當生產後 6 小時）。
- `waste_report(start, end)`：可依日期篩選報廢，每筆有報廢流水號 `id` 與品項 `item_id`。

## 跨組整合

| 對象 | 方式 |
|---|---|
| A2 驗收入庫 | 呼叫 `receive_batch`，不直接改庫存表 |
| B1 生產 | `plan_consumption` + `apply_consumption` 領料，成品以 `item_type='bento'` 入庫 |
| C1 銷售 | 同樣依 FEFO 扣成品批次，並記錄每份成本 |
| C2 財務 | `GET /api/c2/b2-waste` 讀 `waste_report()`，以報廢流水號避免重複計算 |
| A1 預測 | 讀取仍有庫存的原料批次作為可用庫存 |
