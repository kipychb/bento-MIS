[README.md](https://github.com/user-attachments/files/33774780/README.md)
# C2｜營業費用、銷售與經營損益分析（整合版）

## 使用方法

需 Python 3.10+；若要執行 JS 測試，另需 Node.js 18+。**不需 pip 安裝套件。**

從團隊根目錄執行：

```bash
python c2/server.py --port 8002
```

開啟 `http://127.0.0.1:8002`。A1 仍使用 `python a1/server.py`（8000 埠）；B2 原本獨立服務可在 8001 埠執行。不同埠的頁面不會共用瀏覽器資料，因此 C2 透過本機 SQLite 讀取 B2 的 `b2_local.db`，不會直接呼叫不同來源的 API。

C2 費用儲存在 `c2/data/c2.sqlite3`，不會寫入 GitHub。銷售正式資料由 C1 提供，目前支援在 C2 頁面匯入符合格式的 JSON，並寫入 C2 SQLite；B1 單位食材成本寫在每筆銷售的 `items[].unitCost` 中；未串接時報表**僅是示範或部分資料估算，不能當作真實利潤**。

## API

- `GET /api/c2/health`：檢查服務
- `GET /api/c2/expenses`：費用清單
- `POST /api/c2/expenses`：新增費用 `{date,category,amount,description}`
- `PUT /api/c2/expenses/{id}`：修改費用
- `DELETE /api/c2/expenses/{id}`：刪除費用
- `GET /api/c2/b2-waste`：從 B2 SQLite 讀取 `waste_report()` 並轉換格式
- `GET /api/c2/sales`：讀取已匯入銷售資料
- `POST /api/c2/sales`：以 `{sales:[...]}` 依 ID 新增或覆蓋銷售資料

費用分類：`personnel` 人事、`utilities` 水電瓦斯、`rent` 租金、`other` 其他。

## 資料接口

C1 POS 單筆銷售應提供：

```json
{"id":"POS-001","date":"2026-10-21","localTime":"12:30","status":"completed","source":"pos","items":[{"productId":"chicken","productName":"雞腿便當","quantity":2,"unitPrice":70,"unitCost":39}]}
```

`unitCost` 是 B1 的每份**已售**食材成本，不是進貨支出。B2 的報廢損失另扣一次；如成本內含報廢成本，應先統一口徑，避免重複扣除。退款採原 C2 計算方法按比例分配至品項。取消與待處理訂單不列營收。

輸出給 A1 的檔案 `c2-for-a1-sales.json` 為**陣列**：`[{"date":"2026-10-21","product_id":"chicken","slot":"午餐","walk_in_qty":2}]`。`product_id` 必須與 A1 的商品主檔相符。預訂不計入 walk-in，以避免 A1 與確認預訂重複計算。時段分界可在 `src/adapters.mjs` 依 C1 共識調整。

B2 提供 `{total_loss,records:[{batch_id,quantity,loss,reason,operator,created_at}]}`；C2 以 `records[].loss` 計一次，不再加上 `total_loss`。B2 原 API 沒有報廢流水號：目前依整筆欄位建立穩定摘要 ID，如果兩筆資料完全相同可能被視為重複；正式串接建議 B2 加入 `waste_records.id`。

## 測試

```bash
python -m unittest discover -s c2/tests -p 'test_*.py' -v
node --test c2/tests/*.test.mjs
```

## 注意

1. 本版本新增 `c2/`，**未修改 A1 或 B2 原有檔案**，以減少 GitHub merge conflict；A1 主導的共同側邊欄可在最終合併時連結到 C2 URL。
2. B2 的資料庫路徑可由環境變數 `B2_DB_PATH` 調整。
3. 頁面 JSON 匯出含示範資料，並不是 SQL 資料庫備份；資料庫應另外備份。
4. C1、B1 尚未在提供的團隊壓縮檔中，因此**尚未完成正式 API 即時串接**；已提供明確的資料欄位契約。
5. 此為本機教學系統，沒有帳號權限管理，不要直接公開部署到網際網路。
