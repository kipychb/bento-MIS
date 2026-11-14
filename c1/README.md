# C1｜POS 結帳與顧客管理系統 (POS & CRM)

本模組為「台灣版 KITCHEN DIVE 便當店管理資訊系統 (MIS)」的 **C1 子系統**，負責第一線的 **POS 現場快速結帳**、**成品庫存連動扣除**，以及 **顧客預訂（含巨無霸便當）、取餐、取消與意見回饋管理**，並將銷售與時段數據即時匯出供 **C2（經營分析報表）** 與 **A1（需求預測）** 使用。

---

## 🎯 核心功能與 Lean（精實）管理設計

1. **POS 現場快速結帳與防呆扣庫存**
   - 支援現場多品項結帳（現金 / LINE Pay），自動計算總金額並記錄營收。
   - **即時扣除成品庫存**：結帳時先檢查架上成品數量，庫存不足即阻擋交易，避免超賣；結帳成功後立即透過 B2 依 FEFO（先到期先出）扣減成品批次，已超過 6 小時效期的便當不能賣，並記下該批的每份食材成本供 C2 計算毛利。
2. **顧客預訂、取餐與取消系統（巨無霸便當零報廢機制）**
   - 針對高成本之「1公斤巨無霸便當」與「3公斤大胃王定製便當」提供線上預訂，落實 **拉式生產 (Pull System)**，先預訂後製作，將特殊品項報廢損失降為 0。
   - 顧客到店取餐時，系統一鍵將預訂轉為正式 POS 營收並扣除成品庫存；若顧客取消則更新狀態為「已取消」，釋放產能。
3. **顧客意見回饋與 24H 直播防盜整合**
   - 收集顧客星級評分與文字回饋，作為 B1 調整食譜與菜單之依據。
   - 介面整合店內 24 小時 YouTube 實況直播提示，達成零保全成本與社群口碑行銷。
4. **跨組數據拋轉介面（給 A1 & C2）**
   - 自動為每筆交易標記時段（午餐／晚餐／宵夜，與 A1、C2 同一套分界），A1 與 C2 直接從共用資料庫讀取，也可呼叫下方 API。

---

## 📁 檔案結構

```text
c1/
├── README.md        # C1 模組說明文件與 API 規格
├── service.py       # 核心商業邏輯（POS 結帳、預訂狀態機、回饋與資料匯出）
├── server.py        # Flask 伺服器與 RESTful API 路由
├── index.html       # C1 前端操作介面（POS 收銀台 + 預訂與回饋管理）
└── test_c1.py       # 單元測試腳本（驗證結帳扣庫存、預訂取餐與取消邏輯）
```

---

## 🚀 啟動

第一次執行前安裝 Flask，然後在 repo 根目錄啟動：

```bash
pip install flask
```

```bash
python c1/server.py
```

開啟 http://127.0.0.1:5001 。若要先有菜單庫存與歷史資料，先在根目錄執行 `python demo.py --reset`。

## 💾 資料儲存

- 銷售、預訂、回饋存在 repo 根目錄的共用資料庫 `bento.db`（表 `c1_sales`、`c1_sale_items`、`c1_reservations`、`c1_reservation_items`、`c1_feedbacks`），重開伺服器不會消失。
- 菜單與售價來自 B1 品項主檔（`Item` 表）；架上庫存 = B2 中未過期的便當批次數量。
- 品項代號：`chicken_bento`、`tofu_bento`、`giant_1kg`、`giant_3kg`（見 [shared/master.py](../shared/master.py)）。

## 🔌 API

| 方法 | 路徑 | 用途 |
|---|---|---|
| GET | `/api/c1/menu` | 菜單、售價、目前庫存 |
| POST | `/api/c1/checkout` | `{items:[{id,qty}], payment_method}` 結帳 |
| GET / POST | `/api/c1/reservations` | 預訂清單／建立預訂 `{customer_name, phone, pickup_time:"YYYY-MM-DDTHH:MM", items, note}` |
| PATCH | `/api/c1/reservations/<id>` | `{status:"已取餐"｜"已取消"}`；取餐時轉成營收並扣庫存 |
| GET / POST | `/api/c1/feedbacks` | 顧客回饋 |
| GET | `/api/c1/sales?start=&end=` | 給 C2：已完成銷售，每個品項附 `unitCost` |
| GET | `/api/c1/a1` | 給 A1：各時段現場銷量（不含預訂取餐）與預訂 |

## 🧪 測試

```bash
python c1/test_c1.py
```
