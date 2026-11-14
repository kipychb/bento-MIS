"""C1:POS 結帳、顧客預訂／取餐／取消、顧客回饋。

- 資料存在共用資料庫(預設 repo 根目錄的 bento.db),重開伺服器不會消失。
- 菜單與售價來自 B1 品項主檔;成品庫存來自 B2 的便當批次。
- 結帳時透過 B2 依 FEFO 扣成品批次,並記下該批的每份成本,C2 據此計算銷貨成本。
"""
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from b2_inventory import InventoryService, InventoryError  # noqa: E402
from shared.config import INVENTORY_DB, slot_of  # noqa: E402
from shared.master import ensure_master  # noqa: E402

STATUS_TO_A1 = {"預訂中": "confirmed", "已取餐": "fulfilled", "已取消": "cancelled"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS c1_sales (
  id TEXT PRIMARY KEY, sold_at TEXT NOT NULL, date TEXT NOT NULL, local_time TEXT NOT NULL,
  slot TEXT NOT NULL, source TEXT NOT NULL CHECK(source IN ('pos','preorder','history')),
  payment_method TEXT NOT NULL, total_amount REAL NOT NULL, reservation_id TEXT
);
CREATE TABLE IF NOT EXISTS c1_sale_items (
  id INTEGER PRIMARY KEY, sale_id TEXT NOT NULL REFERENCES c1_sales(id),
  product_code TEXT NOT NULL, product_name TEXT NOT NULL, qty INTEGER NOT NULL CHECK(qty>0),
  unit_price REAL NOT NULL, unit_cost REAL NOT NULL, subtotal REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS c1_reservations (
  id TEXT PRIMARY KEY, customer_name TEXT NOT NULL, phone TEXT NOT NULL,
  pickup_date TEXT NOT NULL, pickup_time TEXT NOT NULL, slot TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('預訂中','已取餐','已取消')),
  note TEXT NOT NULL DEFAULT '', total_amount REAL NOT NULL, created_at TEXT NOT NULL, sale_id TEXT
);
CREATE TABLE IF NOT EXISTS c1_reservation_items (
  id INTEGER PRIMARY KEY, reservation_id TEXT NOT NULL REFERENCES c1_reservations(id),
  product_code TEXT NOT NULL, product_name TEXT NOT NULL, qty INTEGER NOT NULL CHECK(qty>0), unit_price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS c1_feedbacks (
  id TEXT PRIMARY KEY, customer_name TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
  comment TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class C1Error(ValueError):
    pass


def _stamp(now):
    return now.replace(microsecond=0).isoformat(sep=" ")


class C1POSService:
    def __init__(self, path=INVENTORY_DB):
        self.inventory = InventoryService(path)
        self.db = self.inventory.db
        ensure_master(self.db)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.inventory.close()

    # ---------- 菜單 ----------
    def get_menu(self, now=None):
        rows = self.db.execute("SELECT code,name,price,made_to_order FROM Item WHERE type='bento' AND price IS NOT NULL ORDER BY item_id").fetchall()
        return [dict(id=r["code"], name=r["name"], price=r["price"], is_giant=bool(r["made_to_order"]),
                     stock=int(self.inventory.available("bento", r["code"], now))) for r in rows]

    def _product(self, code):
        row = self.db.execute("SELECT code,name,price FROM Item WHERE type='bento' AND code=? AND price IS NOT NULL", (code,)).fetchone()
        if row is None:
            raise C1Error(f"找不到品項 {code}")
        return row

    def _lines(self, items):
        """合併同品項並驗證數量。"""
        if not isinstance(items, list) or not items:
            raise C1Error("請至少選擇一項便當")
        merged = {}
        for item in items:
            try:
                qty = int(item.get("qty", 0))
            except (TypeError, ValueError, AttributeError):
                raise C1Error("數量格式錯誤")
            if qty <= 0:
                raise C1Error("數量必須大於 0")
            code = item.get("id")
            self._product(code)
            merged[code] = merged.get(code, 0) + qty
        return merged

    # ---------- POS 結帳 ----------
    def _checkout(self, lines, payment_method, source, now, reservation_id=None):
        """在呼叫端的交易中:FEFO 扣成品、寫銷售紀錄。庫存不足時丟出錯誤,不會改任何資料。"""
        plans, sold = [], []
        for code, qty in lines.items():
            product = self._product(code)
            try:
                plan = self.inventory.plan_consumption("bento", code, qty, now)
            except InventoryError:
                stock = int(self.inventory.available("bento", code, now))
                raise C1Error(f"【{product['name']}】成品庫存不足!(剩餘: {stock})")
            cost = sum(p["quantity"] * p["unit_cost"] for p in plan) / qty
            plans.append(plan)
            sold.append(dict(id=code, name=product["name"], price=product["price"], qty=qty,
                             unit_cost=round(cost, 4), subtotal=product["price"] * qty))
        count = self.db.execute("SELECT COUNT(*) FROM c1_sales WHERE date=?", (now.date().isoformat(),)).fetchone()[0]
        order_id = f"POS-{now:%Y%m%d}-{count + 1:04d}"
        total = sum(s["subtotal"] for s in sold)
        for plan in plans:
            self.inventory.apply_consumption(plan, f"C1 銷售 {order_id}", "C1")
        local_time = now.strftime("%H:%M")
        self.db.execute("INSERT INTO c1_sales(id,sold_at,date,local_time,slot,source,payment_method,total_amount,reservation_id) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (order_id, _stamp(now), now.date().isoformat(), local_time, slot_of(local_time), source,
                         payment_method, total, reservation_id))
        for s in sold:
            self.db.execute("INSERT INTO c1_sale_items(sale_id,product_code,product_name,qty,unit_price,unit_cost,subtotal) "
                            "VALUES(?,?,?,?,?,?,?)", (order_id, s["id"], s["name"], s["qty"], s["price"], s["unit_cost"], s["subtotal"]))
        return dict(order_id=order_id, timestamp=_stamp(now), hour_slot=now.strftime("%H:00"), slot=slot_of(local_time),
                    items=sold, total_amount=total, payment_method=payment_method,
                    source="POS現場結帳" if source == "pos" else "預訂取餐")

    def pos_checkout(self, items, payment_method="cash", now=None):
        """POS 結帳:計算總額、記錄營收、扣除成品庫存。items 格式: [{"id": "chicken_bento", "qty": 2}, ...]"""
        now = now or datetime.now()
        try:
            lines = self._lines(items)
            with self.db:
                record = self._checkout(lines, payment_method, "pos", now)
        except (C1Error, InventoryError) as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "record": record}

    # ---------- 預訂 ----------
    def create_reservation(self, customer_name, phone, pickup_time, items, note="", pickup_date=None, now=None):
        """建立顧客預訂。pickup_time 可以是 'HH:MM' 或 'YYYY-MM-DDTHH:MM'。"""
        now = now or datetime.now()
        try:
            if not customer_name or not phone or not pickup_time:
                raise C1Error("請填寫完整預訂人資訊與取餐時間")
            text = str(pickup_time)
            if "T" in text or " " in text:
                pickup = datetime.fromisoformat(text.replace(" ", "T"))
                pickup_date, pickup_time = pickup.date().isoformat(), pickup.strftime("%H:%M")
            else:
                pickup_time = datetime.strptime(text[:5], "%H:%M").strftime("%H:%M")
                pickup_date = date.fromisoformat(pickup_date).isoformat() if pickup_date else now.date().isoformat()
            if f"{pickup_date} {pickup_time}" < now.strftime("%Y-%m-%d %H:%M"):
                raise C1Error("取餐時間不可早於現在")
            lines = self._lines(items)
        except ValueError as e:
            return {"success": False, "error": str(e) if isinstance(e, C1Error) else "取餐日期或時間格式錯誤"}
        count = self.db.execute("SELECT COUNT(*) FROM c1_reservations").fetchone()[0]
        res_id = f"RES-{now:%m%d}-{count + 1:03d}"
        reserved = []
        for code, qty in lines.items():
            p = self._product(code)
            reserved.append(dict(id=code, name=p["name"], price=p["price"], qty=qty))
        total = sum(r["price"] * r["qty"] for r in reserved)
        with self.db:
            self.db.execute("INSERT INTO c1_reservations(id,customer_name,phone,pickup_date,pickup_time,slot,status,note,total_amount,created_at) "
                            "VALUES(?,?,?,?,?,?,'預訂中',?,?,?)",
                            (res_id, customer_name, phone, pickup_date, pickup_time, slot_of(pickup_time), note or "", total, _stamp(now)))
            for r in reserved:
                self.db.execute("INSERT INTO c1_reservation_items(reservation_id,product_code,product_name,qty,unit_price) VALUES(?,?,?,?,?)",
                                (res_id, r["id"], r["name"], r["qty"], r["price"]))
        return {"success": True, "reservation": self._reservation(res_id)}

    def _reservation(self, res_id):
        r = self.db.execute("SELECT * FROM c1_reservations WHERE id=?", (res_id,)).fetchone()
        if r is None:
            return None
        items = [dict(id=i["product_code"], name=i["product_name"], price=i["unit_price"], qty=i["qty"])
                 for i in self.db.execute("SELECT * FROM c1_reservation_items WHERE reservation_id=? ORDER BY id", (res_id,))]
        return dict(res_id=r["id"], customer_name=r["customer_name"], phone=r["phone"], pickup_date=r["pickup_date"],
                    pickup_time=f"{r['pickup_date']} {r['pickup_time']}", slot=r["slot"], items=items,
                    total_amount=r["total_amount"], status=r["status"], note=r["note"], created_at=r["created_at"],
                    sale_id=r["sale_id"])

    @property
    def reservations(self):
        ids = [r[0] for r in self.db.execute("SELECT id FROM c1_reservations ORDER BY pickup_date DESC, pickup_time DESC, id DESC")]
        return [self._reservation(i) for i in ids]

    def update_reservation_status(self, res_id, new_status, now=None):
        """處理顧客取餐或取消預訂。取餐時轉為營收並扣成品庫存,兩件事在同一個交易裡。"""
        now = now or datetime.now()
        res = self._reservation(res_id)
        if res is None:
            return {"success": False, "error": "找不到該筆預訂紀錄"}
        if res["status"] != "預訂中":
            return {"success": False, "error": f"此訂單狀態已為「{res['status']}」,無法變更。"}
        if new_status == "已取餐":
            lines = {i["id"]: i["qty"] for i in res["items"]}
            try:
                with self.db:
                    record = self._checkout(lines, "預訂取餐付款", "preorder", now, res_id)
                    self.db.execute("UPDATE c1_reservations SET status='已取餐', sale_id=? WHERE id=?", (record["order_id"], res_id))
            except (C1Error, InventoryError) as e:
                return {"success": False, "error": f"{e}。巨無霸便當請先由 B1 生產入庫再取餐。"}
            return {"success": True, "reservation": self._reservation(res_id), "sales_record": record}
        if new_status == "已取消":
            with self.db:
                self.db.execute("UPDATE c1_reservations SET status='已取消' WHERE id=?", (res_id,))
            return {"success": True, "reservation": self._reservation(res_id)}
        return {"success": False, "error": "狀態只能是「已取餐」或「已取消」"}

    # ---------- 回饋 ----------
    def add_feedback(self, customer_name, rating, comment, now=None):
        now = now or datetime.now()
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            return {"success": False, "error": "評分必須是 1 到 5"}
        if not 1 <= rating <= 5:
            return {"success": False, "error": "評分必須是 1 到 5"}
        if not comment:
            return {"success": False, "error": "請輸入回饋內容"}
        count = self.db.execute("SELECT COUNT(*) FROM c1_feedbacks").fetchone()[0]
        fb = dict(fb_id=f"FB-{count + 1:03d}", customer_name=customer_name or "匿名顧客", rating=rating,
                  comment=comment, timestamp=_stamp(now))
        with self.db:
            self.db.execute("INSERT INTO c1_feedbacks(id,customer_name,rating,comment,created_at) VALUES(?,?,?,?,?)",
                            (fb["fb_id"], fb["customer_name"], rating, comment, fb["timestamp"]))
        return {"success": True, "feedback": fb}

    @property
    def feedbacks(self):
        return [dict(fb_id=r["id"], customer_name=r["customer_name"], rating=r["rating"], comment=r["comment"], timestamp=r["created_at"])
                for r in self.db.execute("SELECT * FROM c1_feedbacks ORDER BY created_at DESC, id DESC")]

    # ---------- 歷史資料匯入(舊 POS 紀錄,不扣庫存) ----------
    def import_history(self, rows):
        """匯入舊系統的銷售紀錄,讓 A1 有歷史資料可預測。rows: [{date, local_time, product_code, qty, unit_cost}]"""
        with self.db:
            for n, row in enumerate(rows, 1):
                p = self._product(row["product_code"])
                d = date.fromisoformat(row["date"]).isoformat()
                t = datetime.strptime(row["local_time"], "%H:%M").strftime("%H:%M")
                sale_id = f"HIS-{d.replace('-', '')}-{row['product_code']}-{t.replace(':', '')}-{n}"
                qty = int(row["qty"])
                cur = self.db.execute("INSERT OR IGNORE INTO c1_sales(id,sold_at,date,local_time,slot,source,payment_method,total_amount) "
                                      "VALUES(?,?,?,?,?,'history','歷史匯入',?)", (sale_id, f"{d} {t}:00", d, t, slot_of(t), p["price"] * qty))
                if cur.rowcount == 1:  # 同一筆重複匯入時略過
                    self.db.execute("INSERT INTO c1_sale_items(sale_id,product_code,product_name,qty,unit_price,unit_cost,subtotal) "
                                    "VALUES(?,?,?,?,?,?,?)", (sale_id, p["code"], p["name"], qty, p["price"], float(row["unit_cost"]), p["price"] * qty))
        return len(rows)

    # ---------- 給其他組的資料 ----------
    def sales_for_c2(self, start=None, end=None):
        """C2 匯入格式:{id,date,localTime,status,source,items:[{productId,productName,quantity,unitPrice,unitCost}]}"""
        where, args = [], []
        if start:
            where.append("date>=?"); args.append(start)
        if end:
            where.append("date<=?"); args.append(end)
        sales = self.db.execute("SELECT * FROM c1_sales" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY sold_at,id", args).fetchall()
        out = []
        for s in sales:
            items = [dict(productId=i["product_code"], productName=i["product_name"], quantity=i["qty"],
                          unitPrice=i["unit_price"], unitCost=i["unit_cost"])
                     for i in self.db.execute("SELECT * FROM c1_sale_items WHERE sale_id=? ORDER BY id", (s["id"],))]
            out.append(dict(id=s["id"], date=s["date"], localTime=s["local_time"], status="completed",
                            source=s["source"], paymentMethod=s["payment_method"], items=items))
        return out

    def walk_in_for_a1(self):
        """A1 sales:各日、各品項、各時段的現場銷量。預訂取餐不算現場銷量,避免跟 A1 的預訂重複計算。"""
        rows = self.db.execute("SELECT s.date, i.product_code, s.slot, SUM(i.qty) qty FROM c1_sales s JOIN c1_sale_items i ON i.sale_id=s.id "
                               "WHERE s.source IN ('pos','history') GROUP BY s.date, i.product_code, s.slot ORDER BY s.date").fetchall()
        return [dict(date=r[0], product_id=r[1], slot=r[2], walk_in_qty=int(r[3])) for r in rows]

    def preorders_for_a1(self):
        rows = self.db.execute("SELECT r.pickup_date, i.product_code, r.slot, SUM(i.qty), r.status FROM c1_reservations r "
                               "JOIN c1_reservation_items i ON i.reservation_id=r.id GROUP BY r.id, i.product_code").fetchall()
        return [dict(date=r[0], product_id=r[1], slot=r[2], qty=int(r[3]), status=STATUS_TO_A1[r[4]]) for r in rows]

    def get_summary_for_c2_and_a1(self):
        """C1 頁面用的摘要:累計營收、單數、進行中的預訂、回饋。"""
        total = self.db.execute("SELECT COALESCE(SUM(total_amount),0), COUNT(*) FROM c1_sales WHERE source!='history'").fetchone()
        return {
            "total_revenue": total[0],
            "total_orders": total[1],
            "sales_records": self.sales_for_c2(),
            "active_reservations": [r for r in self.reservations if r["status"] == "預訂中"],
            "feedbacks": self.feedbacks,
        }
