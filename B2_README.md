# B2 庫存、效期、盤點與報廢模組

獨立 Python 3 + SQLite 模組，不需第三方套件。主專案尚未決定技術架構，先避免更動其他組員程式。

## 已實作
- 原料及便當成品的分批庫存（批次編號唯一、有效期限、單價、庫存數量）
- 即將到期及已逾期提醒（僅顯示仍有庫存的批次）
- 盤點差異記錄與庫存調整
- 報廢扣庫、原因、操作人與報廢金額彙總
- 庫存異動日誌；資料庫交易保護

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

## 跨組整合須確認
- A2 進貨驗收：呼叫 receive_batch，不要直接改總庫存。
- B1 生產與 C1 銷售：尚未串接；需共用批次資料與交易記錄，避免重複扣庫。
- C2 財務：可由 waste_report 取得報廢損失。
- 尚未提供網頁 UI 或 API；確定共同框架後再整合。
