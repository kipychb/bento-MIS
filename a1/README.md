# A1｜需求預測與採購量計算

本模組可獨立運作；目前 repo 尚未建立共用後端，因此使用 Python 標準函式庫 + SQLite + HTML/CSS/JavaScript。只處理 A1 計算與計畫快照，不代替 A2 正式採購單、B 組庫存帳或 C 組 POS。無第三方安裝依賴。

## 啟動

安裝 Python 3.10 以上，在 repo 根目錄執行：

```bash
python a1/server.py
```

Windows 若找不到 python 可用 `py a1/server.py`。瀏覽器打開 http://127.0.0.1:8000 。不要直接雙擊 HTML，也不要使用 Live Server，畫面需要本機 API。若埠被占用：`python a1/server.py --port 8001`。

## 操作展示

1. 啟動後會載入明確標示的模擬資料（兩款便當、四種原料、三個時段、28 天歷史資料）。
2. 選計畫日期、特殊日期倍率、原料緩衝比例，按「計算需求與採購」。
3. 查看每款便當各時段的現場需求、預訂量、建議製作量；查看各原料採購建議與交期／容量提醒。
4. 將倍率調成 1.5，重新計算，確認現場需求增加、預訂量保持原數字。
5. 在「資料與串接設定」修改原料 expires_on 為計畫日期，確認庫存被排除。
6. 儲存計畫後重啟服務，依然可載入紀錄；匯出結果 JSON 可交 A2 使用。
7. 原料採購建議不會自動下單、領料或改變庫存。

輸入欄位或 JSON 有變更時會清除舊結果，必須重算才能儲存。SQLite 自動建立在 `a1/data/plans.sqlite3`，僅儲存輸入與計算結果快照。

## 模型與公式

- 計畫範圍：單日，時段可自行設定；預設午餐、晚餐、宵夜。
- 歷史窗口：計畫日前 28 天，且只使用早於計算日的完整日資料；未來銷量不參與計算。
- 優先選同星期的觀測；若沒有則取最近 7 筆。從舊至新權重為 1、2、3…，加權平均 = Σ(銷量 × 權重) / Σ權重。
- 現場需求 = ceil(加權平均 × 特殊日期倍率)。倍率 0 < factor <= 3。
- 建議製作 = 現場需求 + 當日當時段 confirmed 預訂量。歷史輸入必須僅包含 walk_in_qty，不能把預訂取餐再當成現場銷售。
- 原料需求 = Σ(每款便當建議製作份數 × 該原料每份生料用量)。不同便當共用原料會合併計算。
- 原料緩衝 = 原料需求 × buffer。此為政策設定（0–100%），不是估計服務水準的統計安全庫存。
- 可用原料 = 到計畫日仍有效的庫存 − 已保留量。
- 有效在途 = confirmed、計算日至計畫日之間到貨、到計畫日仍有效的原料。received 已算入現有庫存，不再加一次；逾期未到貨須 A2 更新 arrival_date 才納入。
- 淨缺口 = max(0, 原料需求 + 緩衝 − 可用原料 − 有效在途)。建議採購 = ceil(淨缺口 / 包裝量) × 包裝量。
- 最晚下單日 = 計畫日 − lead_days（曆日）。晚於下單期限或超出容量均提醒，不自動縮減採購而掩蓋缺料。

無歷史資料時僅計入預訂並提醒人工補估；缺資料不等於零銷售，真實零銷售必須顯式記錄 0。售罄會使歷史銷量低於真實需求，第一版未做失銷估計。

## 串接 API

所有日期為 YYYY-MM-DD。API 以執行電腦的本地日期作計算日，展示時請確認電腦日期／時區為台灣。金額不在 A1 計算範圍。

| 方法 | 路徑 | 用途 |
|---|---|---|
| GET | /api/a1/sample | 可直接使用的完整範例輸入 |
| POST | /api/a1/calculate | 驗證資料並計算，不寫入庫存或計畫 |
| POST | /api/a1/plans | 重新計算並儲存快照，回傳 id 與 result |
| GET | /api/a1/plans | 最近 30 筆計畫摘要 |
| GET | /api/a1/plans/{id} | 原始輸入與結果快照 |

POST 請求結構：

```json
{"target":"2026-10-26","factor":1,"buffer":0.1,"data":{"products":[],"ingredients":[],"slots":[],"sales":[],"preorders":[],"stock":[],"incoming":[]}}
```

上面僅示範結構，至少需要一款商品、原料與時段；完整可執行輸入請在頁面按「下載輸入範例」。驗證錯誤回傳 HTTP 400 與 `{"error":"原因"}`。

### 各組提供欄位

| 陣列 | 提供組別 | 每列必要欄位 |
|---|---|---|
| products | B1 | id, name, recipe（原料 id → 每份用量） |
| ingredients | B2 / A2 | id, name, unit, pack_size, lead_days, capacity |
| slots | C / 全組 | 不重複時段名稱陣列 |
| sales | C1/C2 | date, product_id, slot, walk_in_qty |
| preorders | C1 | date, product_id, slot, qty, status |
| stock | B2 | ingredient_id, qty, reserved_qty, expires_on |
| incoming | A2 | ingredient_id, qty, arrival_date, expires_on, status |

- 數量必須為非負有限數；便當份數必須為整數。銷售每個日期＋商品＋時段只能一列。
- 每款原料的 recipe、qty、reserved_qty、capacity、pack_size 必須使用同一 base unit；例如生米統一 g，禁止部分 kg、部分 g。recipe 是生料需求，不是煮熟後重量，應已包含正常製程耗損。
- expires_on 採「自該日起不可使用」語意，計畫日等於截止日也排除；是保守的日級示範，正式系統須以批次實際使用時間判斷。
- stock 是查詢當下快照，reserved_qty 應是其他已承諾計畫占用量；不可再次把本次需求保留量扣掉。
- preorders 狀態：confirmed、cancelled、fulfilled；僅 confirmed 算入未履行需求。傳入資料須由 C 組按訂單去重，不含已完成取餐。
- incoming 狀態：confirmed、cancelled、received；上游應按採購明細去重，qty 是未到貨餘量。
- 本模組計算整日新製作需求，不扣前一天的成品便當；不會跨日繼續販售舊便當。

### A1 回傳給其他組

- `forecasts` → B1：product_id, slot, total（建議製作）、baseline, walk_in, preorders, samples, method。
- `purchases` → A2：ingredient_id, unit, demand, buffer, available, excluded, incoming, shortage, purchase, pack_size, latest_order_date, warnings。
- `warnings` → 店長首頁：缺少歷史、樣本不足。
- A2 必須人工確認採購建議後建立真正採購單；快照 id 不是採購單 id。
- 可直接匯入 `core.calculate(data, target, factor, buffer)` 到 Python 共用後端，或用上方 HTTP JSON 介面串接其他語言後端。讀取正式資料來源的 adapter 由共同後端決定後串接，現在使用完整 JSON 輸入。

## 測試

```bash
python -m unittest discover -s a1 -v
```

測試涵蓋預測公式、活動倍率與預訂分離、過期／保留庫存、在途過濾、包裝進位、無歷史、零銷量、共用原料、交期容量警示、輸入驗證及 API 儲存讀回。使用暫存 SQLite，不污染實際計畫紀錄。

## 第一版限制

這是課堂本機操作模組，僅監聽 127.0.0.1，未提供登入或公網部署。它不是生產環境的食品效期控制系統。A/B/C 模組目前尚未存在，無法宣稱已完成全系統串接。

容量是有效可用量＋在途＋新採購的單日保守估算，不是每個小時的庫位排程；沒有多日消耗、在途到貨時間與烹調時間聯合排程。未納入季節性學習、天氣模型或新商品人工基準，需店長檢視無樣本提示。尚未做預測回測，不能宣稱準確率或降本百分比。
