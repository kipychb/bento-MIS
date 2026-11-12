# bento-MIS 便當店管理資訊系統

模仿日本 KITCHEN DIVE 的「超便宜、份量超大」便當店，用 MIS 把管理成本降到最低。
閉環流程：**銷售 → 預測 → 採購 → 生產 → 銷售**。

## 技術

Python 3 + SQLite，B1、B2 模組不需安裝第三方套件。

## 分工

| 組別 | 負責內容 | 資料夾／檔案 |
|---|---|---|
| A 組 | 需求預測、採購、供應商、驗收入庫（SCM） | `a1/`、`a2` |
| B1 | 食譜、分批生產、食材成本 | `b1_*.py` |
| B2 | 庫存、效期、盤點、報廢 | `b2_inventory/`、`b2_app.py`、`B2_README.md` |
| C 組 | POS、顧客、費用與經營報表 | `c1/`、`c2/` |

## B1 模組：食譜、生產與食材成本

| 檔案 | 功能 |
|---|---|
| `b1_db.py` | 建立四張表：Item（品項主檔）、Recipe（食譜）、ProductionBatch（生產批次）、BatchMaterialUsage（領料明細） |
| `b1_production.py` | 新增品項與食譜、分批生產：FEFO 扣原料、計算批次成本、成品入庫 |
| `b1_report.py` | 標準成本 vs 實際成本，差異拆成「用量差異」與「價格差異」 |
| `b1_export.py` | 給 C2 的成本摘要，並整合 B2 的報廢與盤點，算出「有效成本」 |
| `b1_menu.py` | 文字選單，把以上功能串起來，可直接展示 |

### 怎麼執行

在專案根目錄：

```bash
python b1_menu.py        # 文字選單（輸入 9 載入示範資料，5 生產，6 差異報表）
python b1_production.py  # 生產與 FEFO 示範
python b1_report.py      # 差異報表示範
python b1_export.py      # 成本與損耗摘要示範
```

示範程式使用獨立的 `*_demo.db`，不會弄亂正式資料。選單使用 `bento.db`。

### 核心規則

- **FEFO**：先到期的原料批次先使用，已過期的不使用。
- **用量換算**：原料需求量 = 每份用量 × 產出份數 ÷ (1 − 損耗率)。
- **批次成本** = Σ(領用量 × 該原料批次單價)；每份成本 = 批次成本 ÷ 產出份數。
- **有效成本** = (生產成本 + 原料報廢與盤點短少) ÷ 實際賣得出去的份數。
- 生產過程為單一交易：扣料、記錄、成品入庫，全部成功或全部不做。

### 給其他組的介面

| 對象 | 介面 |
|---|---|
| C2 | `from b1_export import cost_summary_for_c2`，傳入 `InventoryService`，取得生產成本、報廢損失、報廢率、有效成本 |
| C1 | 成品庫存在 `inventory_batches`（`item_type='bento'`），每份成本在 `ProductionBatch.unit_cost` |
| B2 | 使用 B2 的 `InventoryService`。`Item.code` 必須與 B2 的 `item_id` 完全一致 |
| A1 | 食譜在 `Recipe` 表，可用來由預測製作量反推原料需求 |

### 已知限制與待改進

- 領料目前由 B1 直接扣 `inventory_batches`，待 B2 提供正式 `consume()` 後替換。
- B2 效期只存「日期」，成品 6 小時的效期無法精確表示，需加時間欄位。
- B1 選單使用 `bento.db`，B2 網頁版使用 `b2_local.db`，整合時需統一資料庫檔。
- 報廢與盤點損失目前沒有日期篩選，尚不能按月統計。
- 食譜用量單位須與 B2 的固定庫存單位相同（例如都用 kg）。

## B2 模組

詳見 [B2_README.md](B2_README.md)。
