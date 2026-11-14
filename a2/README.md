# A2｜供應商、採購單、分批到貨與驗收入庫

Python 標準函式庫 + SQLite + 單頁 HTML，無第三方套件。A2 接收 A1 的採購建議，經人工比價後建立正式採購單，追蹤分批到貨，驗收合格的原料透過 B2 的 `receive_batch` 寫入庫存。

## 啟動

在 repo 根目錄執行（需 Python 3.10+）：

```bash
python a2/server.py
```

開啟 http://127.0.0.1:8003 （A1 用 8000、B2 用 8001、C2 用 8002）。第一次開啟時資料庫是空的，頁面會詢問是否載入名稱標示「（模擬）」的示範供應商與報價；原料 id 跟 A1 範例一致（rice、chicken、vegetable、tofu，單位 g）。

- A2 資料：`a2/data/a2.sqlite3`（已加入 .gitignore），可用環境變數 `A2_DB_PATH` 改路徑
- B2 庫存：預設寫入 repo 根目錄的 `b2_local.db`，也就是 `b2_app.py` 和 C2 讀取的同一個檔案；可用 `B2_DB_PATH` 改路徑

## 為什麼這樣設計（Lean 採購）

KITCHEN DIVE 式的便當店靠「量大、毛利薄」生存，採購端要做到三件事：**買得便宜、不囤貨、不斷料**。

| 問題 | A2 的做法 |
|---|---|
| 量大才有折扣，但生鮮放不久 | 報價採**數量級距**（買滿 N 包全部適用該價）。比價時同時列出「再多買到下一級距」的總價與單位成本，並提醒多買的量須在保存天數內用完，交給人判斷，不自動加量 |
| 便宜的供應商交期太長 | 比價排序：**準時到貨優先**，其次總金額，再來單位成本。來不及的報價標「來不及」 |
| 冷藏庫容量有限 | 採購單可**分批到貨**；單日到貨量超過原料容量時直接擋下，頁面可一鍵平均拆批（每批上限 N 包、每 N 天一批） |
| 供應商最低訂購金額 | 比價時提示「需併單」；未達金額的採購單不能確認下單 |
| 到貨品質不穩 | 驗收四項檢查（溫度、包裝、外觀氣味、標示效期）全部通過才可允收；拒收必填原因；效期已到不可入庫 |
| 短少／拒收後斷料 | 驗收時可填補貨日，系統自動新增一批補貨到貨，A1 在途立即反映 |
| 發票價格跟採購單不同 | 驗收記錄實際單價，計算價差並列入供應商評分 |
| 該跟誰買 | 報表計算每家供應商的準時率、允收率、價差與進貨金額，作為下次比價與開發新供應商的依據 |

## 流程與狀態

```
A1 採購建議 ──► 比價 ──► 採購單草稿 ──確認下單──► 已下單 ──驗收一部分──► 部分到貨 ──全部驗收──► 已到貨
                                  │                    │                     │
                                  └──取消──► 已取消 ◄──┘                     └──結案（取消未到批次）──► 已結案
```

- 只有「已下單／部分到貨」的待到批次會出現在驗收清單，並輸出給 A1 當在途。
- 每個到貨批次只能驗收一次；超過應到包數的部分必須拒收退回。
- 允收的包數 × 包裝量 = 入庫數量（基本單位），每包單價 ÷ 包裝量 = 單位成本，一次驗收在 B2 產生一個批次，批號預設 `A2-{採購單號}-D{到貨批次 id}`，也可以自行輸入。
- 所有檢查都先完成才呼叫 B2，B2 寫入成功後才寫 A2 驗收紀錄，避免 B2 有批次但 A2 查不到來源。兩邊是不同 SQLite 檔，無法同一個交易；若 B2 成功後 A2 寫入失敗（例如磁碟錯誤），要人工比對 `b2_batch_id`。

## 頁面

| 分頁 | 功能 |
|---|---|
| 到貨與驗收 | 今日應到、逾期、待確認草稿、在途數；待到貨清單（驗收、改期）；最近驗收紀錄 |
| 比價與建單 | 貼上或選擇 A1 匯出的 JSON，逐項比價；或手動輸入原料、數量、需要到貨日比價；從任一報價建立採購單草稿（可分批） |
| 採購單 | 依狀態篩選；確認下單、取消、結案；每行顯示分批到貨與允收數 |
| 供應商與報價 | 新增／停用供應商；新增或更新報價級距；原料主檔 |
| 報表與匯出 | 期間內的供應商評分與進貨金額；下載 A1 在途、A1 原料、C2 進貨支出、驗收紀錄 JSON |

## API

回應格式：成功 `{"result": ...}`，錯誤 HTTP 400/404 加 `{"error": "原因"}`。日期都是 `YYYY-MM-DD`。

| 方法 | 路徑 | 用途 |
|---|---|---|
| GET | /api/a2/ingredients | 原料主檔 |
| POST | /api/a2/ingredients | 新增或更新 `{id,name,unit,shelf_life_days,capacity}` |
| GET | /api/a2/suppliers | 供應商與報價級距 |
| POST | /api/a2/suppliers | 新增 `{name,lead_days,min_order_amount,contact,phone}` |
| POST | /api/a2/suppliers/{id}/active | `{active:true/false}` |
| POST | /api/a2/prices | 新增或更新級距 `{supplier_id,ingredient_id,pack_size,min_packs,price_per_pack}` |
| POST | /api/a2/prices/{id}/delete | 刪除級距 |
| GET | /api/a2/compare?ingredient_id=&qty=&need_by=&order_date= | 比價 |
| POST | /api/a2/a1-suggestions | `{result: A1 計算結果, order_date}` → 每項 purchase>0 的比價 |
| POST | /api/a2/split | `{packs,first_date,max_packs,interval_days}` → 分批計畫 |
| GET | /api/a2/pos?status= | 採購單列表 |
| GET | /api/a2/pos/{id} | 單張採購單 |
| POST | /api/a2/pos | 建立草稿，見下方 |
| POST | /api/a2/pos/{id}/confirm | 確認下單 |
| POST | /api/a2/pos/{id}/cancel | `{reason}` 取消（未到貨）或結案（部分到貨） |
| GET | /api/a2/schedule?start=&end= | 待到貨批次 |
| POST | /api/a2/deliveries/{id}/reschedule | `{date,reason}` 改期 |
| POST | /api/a2/deliveries/{id}/receive | 驗收，見下方 |
| GET | /api/a2/receipts | 驗收紀錄 |
| GET | /api/a2/incoming | → A1 |
| GET | /api/a2/a1-ingredients | → A1 |
| GET | /api/a2/spend?start=&end= | → C2 |

建立採購單：

```json
{"supplier_id":1,"created_by":"A2","order_date":"2026-10-28","source":"A1",
 "lines":[{"ingredient_id":"rice","packs":12,"pack_size":5000,
           "deliveries":[{"date":"2026-10-30","packs":6},{"date":"2026-11-01","packs":6}]}]}
```

`price_per_pack` 省略時依包數套用級距價；`deliveries` 省略時在「下單日 + 供應商交期」一次到齊。

驗收：

```json
{"received_packs":6,"accepted_packs":5,"expiry_date":"2027-03-01","inspector":"小明",
 "checks":{"temperature":true,"package":true,"appearance":true,"label":true},
 "reject_reason":"破袋","supplier_lot":"LOT-88","price_per_pack":212,
 "received_date":"2026-10-30","backorder_date":"2026-11-02"}
```

## 跨組資料

| 對象 | 內容 |
|---|---|
| A1 → A2 | A1 計畫結果的 `target` 與 `purchases[]`（ingredient_id、purchase、latest_order_date、warnings）。A2 只做比價，人工確認後才建單 |
| A2 → A1 `incoming` | `{ingredient_id, qty, arrival_date, expires_on, status:"confirmed"}`，qty 為尚未到貨的基本單位數量。已驗收的不再輸出，因為已算進 B2 庫存。`expires_on` 是「到貨日 + 保存天數」的估計值 |
| A2 → A1 `ingredients` | `{id,name,unit,pack_size,lead_days,capacity}`，pack_size 和 lead_days 取單位成本最低的啟用中報價 |
| A2 → B2 | 呼叫 `InventoryService.receive_batch("ingredient", item_id, batch_code, expiry_date, qty, unit, unit_cost, operator)`，不直接改 B2 資料表 |
| A2 → C2 | `spend` 的 `records[]`：日期、供應商、採購單、原料、允收數量、金額（允收包數 × 實際單價）。這是**進貨支出**；C2 的銷貨成本仍用 B1 每份食材成本，兩者不可相加，否則食材會被算兩次 |
| B1 | 正式版應由 B1 提供原料主檔；A2 目前保留本地主檔，id 與單位必須跟 B1、B2 一致 |

單位：原料的基本單位必須是 B2 支援的 g、kg、mL、L、顆、份、包之一，且與 A1 食譜、B2 庫存使用同一單位。已有報價或採購紀錄的原料不能改單位。

## 測試

```bash
cd a2
python -m unittest test_a2 -v
```

23 項測試涵蓋：級距價與下一級距提示、包裝進位與最低起訂、交期排序、停用供應商、A1 匯入、採購單編號與預設到貨、分批計畫與合計檢查、容量限制、最低訂購金額、在途只計已下單、改期、取消／結案、驗收寫入 B2（數量與單位成本）、分批部分到貨、拒收原因與補貨、檢查未過／過期不可允收、超收與重複驗收、草稿不可驗收、供應商評分與期間篩選，以及 HTTP 完整流程（使用暫存資料庫）。

## 限制

- 課堂本機版，只監聽 127.0.0.1，沒有登入與權限；採購單「確認下單」不會真的傳給供應商。
- 沒有發票／應付帳款與付款，沒有退貨單據（拒收只記錄在驗收單）。
- 一張採購單建議只放同一供應商；目前頁面從比價結果一次建一行，多品項併單可用 API 建立。
- 效期在驗收時由人工輸入；在途的 `expires_on` 只是估計。
