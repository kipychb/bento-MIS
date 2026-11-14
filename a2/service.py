"""A2 suppliers, purchase orders, split deliveries and receiving (SQLite, standard library).

Quantities on purchase orders are counted in supplier packs; pack_size converts a pack
to the ingredient's base unit (the same unit A1 and B2 use, e.g. g).
"""
import json
import math
import sqlite3
from datetime import date, timedelta

UNITS = {"g", "kg", "mL", "L", "顆", "份", "包"}  # same set B2 accepts
PO_STATUS = ("draft", "ordered", "partial", "received", "closed", "cancelled")
CHECKS = ("temperature", "package", "appearance", "label")  # 驗收檢查項目


class PurchaseError(ValueError):
    pass


def number(value, label, integer=False, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PurchaseError(f"{label} 必須是有限數字")
    if value < 0 or (positive and value <= 0) or (integer and value != int(value)):
        raise PurchaseError(f'{label} 必須是{"正" if positive else "非負"}{"整數" if integer else "數字"}')
    return int(value) if integer else float(value)


def day(value, label="日期"):
    if isinstance(value, date):
        return value
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise PurchaseError(f"{label} 必須為 YYYY-MM-DD")
    if result.isoformat() != value:
        raise PurchaseError(f"{label} 必須為 YYYY-MM-DD")
    return result


def text(value, label, required=True):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise PurchaseError(f"{label} 必填")
    return value.strip()


SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredients (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, unit TEXT NOT NULL,
  shelf_life_days INTEGER NOT NULL CHECK(shelf_life_days>0),
  capacity REAL NOT NULL CHECK(capacity>=0)
);
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, contact TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '', lead_days INTEGER NOT NULL CHECK(lead_days>=0),
  min_order_amount REAL NOT NULL DEFAULT 0 CHECK(min_order_amount>=0),
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS supplier_prices (
  id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  ingredient_id TEXT NOT NULL REFERENCES ingredients(id),
  pack_size REAL NOT NULL CHECK(pack_size>0), min_packs INTEGER NOT NULL CHECK(min_packs>=1),
  price_per_pack REAL NOT NULL CHECK(price_per_pack>=0),
  UNIQUE(supplier_id, ingredient_id, pack_size, min_packs)
);
CREATE TABLE IF NOT EXISTS purchase_orders (
  id INTEGER PRIMARY KEY, po_no TEXT NOT NULL UNIQUE, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  status TEXT NOT NULL, order_date TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual', created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS po_lines (
  id INTEGER PRIMARY KEY, po_id INTEGER NOT NULL REFERENCES purchase_orders(id),
  ingredient_id TEXT NOT NULL REFERENCES ingredients(id),
  packs INTEGER NOT NULL CHECK(packs>0), pack_size REAL NOT NULL CHECK(pack_size>0),
  price_per_pack REAL NOT NULL CHECK(price_per_pack>=0)
);
CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY, line_id INTEGER NOT NULL REFERENCES po_lines(id),
  scheduled_date TEXT NOT NULL, packs INTEGER NOT NULL CHECK(packs>0),
  status TEXT NOT NULL CHECK(status IN ('pending','received','cancelled')),
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY, delivery_id INTEGER NOT NULL UNIQUE REFERENCES deliveries(id),
  received_date TEXT NOT NULL, received_packs INTEGER NOT NULL, accepted_packs INTEGER NOT NULL,
  rejected_packs INTEGER NOT NULL, reject_reason TEXT NOT NULL DEFAULT '',
  supplier_lot TEXT NOT NULL DEFAULT '', batch_code TEXT, expiry_date TEXT,
  price_per_pack REAL NOT NULL, checks_json TEXT NOT NULL, inspector TEXT NOT NULL,
  b2_batch_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class PurchasingService:
    """inventory: object with B2's receive_batch(...) signature; None skips stock-in (tests/demo)."""

    def __init__(self, path="a2.sqlite3", inventory=None):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.inventory = inventory

    def close(self):
        self.db.close()

    def _rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def _one(self, sql, args, label):
        row = self.db.execute(sql, args).fetchone()
        if row is None:
            raise PurchaseError(f"{label}不存在")
        return dict(row)

    # ---------- master data ----------
    def upsert_ingredient(self, id, name, unit, shelf_life_days, capacity):
        id, name = text(id, "原料 id"), text(name, "原料名稱")
        if unit not in UNITS:
            raise PurchaseError("單位必須與 B2 一致：" + "、".join(sorted(UNITS)))
        shelf = number(shelf_life_days, "保存天數", integer=True, positive=True)
        capacity = number(capacity, "倉儲容量")
        old = self.db.execute("SELECT unit FROM ingredients WHERE id=?", (id,)).fetchone()
        if old and old["unit"] != unit and self.db.execute(
                "SELECT 1 FROM supplier_prices WHERE ingredient_id=? UNION SELECT 1 FROM po_lines WHERE ingredient_id=?", (id, id)).fetchone():
            raise PurchaseError("已有報價或採購紀錄的原料不可改單位")
        with self.db:
            self.db.execute("INSERT INTO ingredients(id,name,unit,shelf_life_days,capacity) VALUES(?,?,?,?,?) "
                            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,unit=excluded.unit,"
                            "shelf_life_days=excluded.shelf_life_days,capacity=excluded.capacity",
                            (id, name, unit, shelf, capacity))
        return id

    def ingredients(self):
        return self._rows("SELECT * FROM ingredients ORDER BY id")

    def add_supplier(self, name, lead_days, min_order_amount=0, contact="", phone=""):
        name = text(name, "供應商名稱")
        if self.db.execute("SELECT 1 FROM suppliers WHERE name=?", (name,)).fetchone():
            raise PurchaseError("供應商名稱重複")
        with self.db:
            cur = self.db.execute("INSERT INTO suppliers(name,contact,phone,lead_days,min_order_amount) VALUES(?,?,?,?,?)",
                                  (name, text(contact, "聯絡人", False), text(phone, "電話", False),
                                   number(lead_days, "交期天數", integer=True), number(min_order_amount, "最低訂購金額")))
        return cur.lastrowid

    def set_supplier_active(self, supplier_id, active):
        self._one("SELECT id FROM suppliers WHERE id=?", (supplier_id,), "供應商")
        with self.db:
            self.db.execute("UPDATE suppliers SET active=? WHERE id=?", (1 if active else 0, supplier_id))

    def suppliers(self):
        rows = self._rows("SELECT * FROM suppliers ORDER BY active DESC,id")
        for s in rows:
            s["prices"] = self._rows("SELECT p.*,i.name ingredient_name,i.unit FROM supplier_prices p JOIN ingredients i ON i.id=p.ingredient_id "
                                     "WHERE supplier_id=? ORDER BY ingredient_id,pack_size,min_packs", (s["id"],))
        return rows

    def set_price(self, supplier_id, ingredient_id, pack_size, min_packs, price_per_pack):
        """One price tier: buying at least min_packs packs makes every pack cost price_per_pack."""
        self._one("SELECT id FROM suppliers WHERE id=?", (supplier_id,), "供應商")
        self._one("SELECT id FROM ingredients WHERE id=?", (ingredient_id,), "原料")
        args = (supplier_id, ingredient_id, number(pack_size, "包裝量", positive=True),
                number(min_packs, "級距起訂包數", integer=True, positive=True), number(price_per_pack, "每包單價"))
        with self.db:
            self.db.execute("INSERT INTO supplier_prices(supplier_id,ingredient_id,pack_size,min_packs,price_per_pack) VALUES(?,?,?,?,?) "
                            "ON CONFLICT(supplier_id,ingredient_id,pack_size,min_packs) DO UPDATE SET price_per_pack=excluded.price_per_pack", args)

    def delete_price(self, price_id):
        with self.db:
            if self.db.execute("DELETE FROM supplier_prices WHERE id=?", (price_id,)).rowcount == 0:
                raise PurchaseError("報價不存在")

    # ---------- batch price comparison ----------
    @staticmethod
    def _tier(tiers, packs):
        usable = [t for t in tiers if t["min_packs"] <= packs]
        return max(usable, key=lambda t: t["min_packs"]) if usable else None

    def compare(self, ingredient_id, qty, need_by=None, order_date=None):
        """Rank every active supplier offer for qty base units of an ingredient."""
        ingredient = self._one("SELECT * FROM ingredients WHERE id=?", (ingredient_id,), "原料")
        qty = number(qty, "需求數量", positive=True)
        order_date = day(order_date) if order_date else date.today()
        need_by = day(need_by, "需要到貨日") if need_by else None
        offers = {}
        for p in self._rows("SELECT p.*,s.name supplier_name,s.lead_days,s.min_order_amount FROM supplier_prices p "
                            "JOIN suppliers s ON s.id=p.supplier_id WHERE p.ingredient_id=? AND s.active=1", (ingredient_id,)):
            offers.setdefault((p["supplier_id"], p["pack_size"]), []).append(p)
        result = []
        for (supplier_id, pack_size), tiers in offers.items():
            first = tiers[0]
            need_packs = math.ceil(round(qty / pack_size, 10))
            packs = max(need_packs, min(t["min_packs"] for t in tiers))  # below the lowest tier = buy the minimum
            tier = self._tier(tiers, packs)
            total = round(packs * tier["price_per_pack"], 2)
            arrival = order_date + timedelta(days=first["lead_days"])
            flags = []
            if need_by and arrival > need_by:
                flags.append(f"交期 {first['lead_days']} 天，預計 {arrival} 到貨，晚於需要日")
            if total < first["min_order_amount"]:
                flags.append(f"未達最低訂購金額 {first['min_order_amount']:.0f} 元，需併單")
            if packs > need_packs:
                flags.append(f"最低起訂 {packs} 包，多買 {packs - need_packs} 包")
            # Next tier: show what buying more would save per unit, but warn about shelf life (Lean: 不囤貨).
            upper = sorted((t for t in tiers if t["min_packs"] > packs), key=lambda t: t["min_packs"])
            next_tier = None
            if upper:
                t = upper[0]
                next_total = round(t["min_packs"] * t["price_per_pack"], 2)
                next_tier = dict(packs=t["min_packs"], price_per_pack=t["price_per_pack"], total=next_total,
                                 extra_qty=round((t["min_packs"] - packs) * pack_size, 4),
                                 unit_cost=round(next_total / (t["min_packs"] * pack_size), 6),
                                 note=f"多出的量須在 {ingredient['shelf_life_days']} 天效期內用完才划算")
            result.append(dict(supplier_id=supplier_id, supplier_name=first["supplier_name"], pack_size=pack_size, packs=packs,
                               qty=round(packs * pack_size, 4), price_per_pack=tier["price_per_pack"], total=total,
                               unit_cost=round(total / (packs * pack_size), 6), lead_days=first["lead_days"],
                               arrival_date=arrival.isoformat(), on_time=not (need_by and arrival > need_by),
                               next_tier=next_tier, warnings=flags))
        # On-time offers first, then cheapest total spend, then cheapest unit cost.
        result.sort(key=lambda o: (not o["on_time"], o["total"], o["unit_cost"]))
        for rank, o in enumerate(result, 1):
            o["rank"] = rank
        return dict(ingredient_id=ingredient_id, name=ingredient["name"], unit=ingredient["unit"], qty=qty,
                    need_by=need_by.isoformat() if need_by else None, offers=result)

    def suggestions_from_a1(self, a1_result, order_date=None):
        """Turn A1 `purchases` (purchase>0) into comparisons; nothing is ordered automatically."""
        if not isinstance(a1_result, dict) or not isinstance(a1_result.get("purchases"), list):
            raise PurchaseError("請提供 A1 計算結果（含 purchases 陣列）")
        target = day(a1_result.get("target"), "A1 計畫日期")
        known = {i["id"] for i in self.ingredients()}
        out, missing = [], []
        for p in a1_result["purchases"]:
            if not isinstance(p, dict) or not p.get("purchase"):
                continue
            if p.get("ingredient_id") not in known:
                missing.append(p.get("ingredient_id"))
                continue
            row = self.compare(p["ingredient_id"], p["purchase"], target, order_date)
            # A1 flagged capacity -> suggest split deliveries.
            row["a1_warnings"] = p.get("warnings", [])
            row["latest_order_date"] = p.get("latest_order_date")
            out.append(row)
        return dict(target=target.isoformat(), items=out,
                    warnings=[f"A2 原料主檔沒有 {m}，請先建立" for m in missing])

    # ---------- purchase orders ----------
    def create_po(self, supplier_id, lines, created_by, order_date=None, note="", source="manual"):
        """lines: [{ingredient_id, packs, pack_size, price_per_pack?, deliveries?:[{date, packs}]}]."""
        supplier = self._one("SELECT * FROM suppliers WHERE id=?", (supplier_id,), "供應商")
        if not supplier["active"]:
            raise PurchaseError("供應商已停用")
        created_by = text(created_by, "建立人")
        order_date = day(order_date) if order_date else date.today()
        if not isinstance(lines, list) or not lines:
            raise PurchaseError("採購單至少需要一筆明細")
        prepared = []
        for line in lines:
            if not isinstance(line, dict):
                raise PurchaseError("採購明細格式錯誤")
            ingredient = self._one("SELECT * FROM ingredients WHERE id=?", (line.get("ingredient_id"),), "原料")
            packs = number(line.get("packs"), "訂購包數", integer=True, positive=True)
            pack_size = number(line.get("pack_size"), "包裝量", positive=True)
            tiers = self._rows("SELECT * FROM supplier_prices WHERE supplier_id=? AND ingredient_id=? AND pack_size=?",
                               (supplier_id, ingredient["id"], pack_size))
            if line.get("price_per_pack") is not None:
                price = number(line["price_per_pack"], "每包單價")
            else:
                tier = self._tier(tiers, packs)
                if tier is None:
                    raise PurchaseError(f"{ingredient['name']} 沒有此供應商 {pack_size:g}{ingredient['unit']} 包裝的適用報價")
                price = tier["price_per_pack"]
            plan = line.get("deliveries") or [dict(date=(order_date + timedelta(days=supplier["lead_days"])).isoformat(), packs=packs)]
            if not isinstance(plan, list):
                raise PurchaseError("分批到貨格式錯誤")
            plan = [dict(date=day(d.get("date"), "到貨日"), packs=number(d.get("packs"), "到貨包數", integer=True, positive=True))
                    for d in plan if isinstance(d, dict)]
            if sum(d["packs"] for d in plan) != packs:
                raise PurchaseError(f"{ingredient['name']} 分批到貨包數合計必須等於訂購包數 {packs}")
            if any(d["date"] < order_date for d in plan):
                raise PurchaseError("到貨日不可早於下單日")
            per_day = {}
            for d in plan:
                per_day[d["date"]] = per_day.get(d["date"], 0) + d["packs"] * pack_size
            if ingredient["capacity"] and max(per_day.values()) > ingredient["capacity"]:
                raise PurchaseError(f"{ingredient['name']} 單日到貨量超過倉儲容量 {ingredient['capacity']:g}{ingredient['unit']}，請分批")
            prepared.append((ingredient["id"], packs, pack_size, price, plan))
        with self.db:
            count = self.db.execute("SELECT COUNT(*) FROM purchase_orders WHERE order_date=?", (order_date.isoformat(),)).fetchone()[0]
            po_no = f"PO{order_date:%Y%m%d}-{count + 1:03d}"
            cur = self.db.execute("INSERT INTO purchase_orders(po_no,supplier_id,status,order_date,note,source,created_by) VALUES(?,?,?,?,?,?,?)",
                                  (po_no, supplier_id, "draft", order_date.isoformat(), text(note, "備註", False), source, created_by))
            po_id = cur.lastrowid
            for iid, packs, pack_size, price, plan in prepared:
                line_id = self.db.execute("INSERT INTO po_lines(po_id,ingredient_id,packs,pack_size,price_per_pack) VALUES(?,?,?,?,?)",
                                          (po_id, iid, packs, pack_size, price)).lastrowid
                for d in sorted(plan, key=lambda d: d["date"]):
                    self.db.execute("INSERT INTO deliveries(line_id,scheduled_date,packs,status) VALUES(?,?,?,'pending')",
                                    (line_id, d["date"].isoformat(), d["packs"]))
        return self.po(po_id)

    @staticmethod
    def split_plan(packs, first_date, max_packs, interval_days=1):
        """Even split helper for 分批到貨: at most max_packs per delivery, every interval_days."""
        packs = number(packs, "訂購包數", integer=True, positive=True)
        max_packs = number(max_packs, "每批上限包數", integer=True, positive=True)
        interval = number(interval_days, "間隔天數", integer=True)
        first = day(first_date, "首批到貨日")
        n = math.ceil(packs / max_packs)
        base, extra = divmod(packs, n)
        return [dict(date=(first + timedelta(days=i * interval)).isoformat(), packs=base + (1 if i < extra else 0)) for i in range(n)]

    def po(self, po_id):
        po = self._one("SELECT o.*,s.name supplier_name,s.min_order_amount FROM purchase_orders o JOIN suppliers s ON s.id=o.supplier_id WHERE o.id=?",
                       (po_id,), "採購單")
        po["lines"] = self._rows("SELECT l.*,i.name ingredient_name,i.unit FROM po_lines l JOIN ingredients i ON i.id=l.ingredient_id WHERE po_id=? ORDER BY l.id", (po_id,))
        for line in po["lines"]:
            line["qty"] = round(line["packs"] * line["pack_size"], 4)
            line["amount"] = round(line["packs"] * line["price_per_pack"], 2)
            line["deliveries"] = self._rows("SELECT d.*,r.accepted_packs,r.rejected_packs,r.received_date,r.batch_code FROM deliveries d "
                                            "LEFT JOIN receipts r ON r.delivery_id=d.id WHERE line_id=? ORDER BY scheduled_date,d.id", (line["id"],))
            line["accepted_packs"] = sum(d["accepted_packs"] or 0 for d in line["deliveries"])
        po["amount"] = round(sum(l["amount"] for l in po["lines"]), 2)
        po["warnings"] = ["未達供應商最低訂購金額"] if po["amount"] < po["min_order_amount"] else []
        return po

    def pos(self, status=None):
        if status and status not in PO_STATUS:
            raise PurchaseError("狀態錯誤")
        ids = [r["id"] for r in self.db.execute("SELECT id FROM purchase_orders" + (" WHERE status=?" if status else "") + " ORDER BY id DESC",
                                                 (status,) if status else ())]
        return [self.po(i) for i in ids]

    def confirm_po(self, po_id):
        """draft -> ordered: the PO has been sent to the supplier and its deliveries count as incoming."""
        po = self.po(po_id)
        if po["status"] != "draft":
            raise PurchaseError("只有草稿可以確認下單")
        if po["warnings"]:
            raise PurchaseError("未達最低訂購金額，請併入其他品項後再下單")
        with self.db:
            self.db.execute("UPDATE purchase_orders SET status='ordered' WHERE id=?", (po_id,))
        return self.po(po_id)

    def cancel_po(self, po_id, reason):
        """Cancel a PO with no receipt, or close a partially received one (remaining deliveries cancelled)."""
        reason = text(reason, "取消原因")
        po = self.po(po_id)
        if po["status"] in ("received", "closed", "cancelled"):
            raise PurchaseError("此採購單已結案")
        new = "closed" if po["status"] == "partial" else "cancelled"
        with self.db:
            self.db.execute("UPDATE deliveries SET status='cancelled',note=? WHERE status='pending' AND line_id IN (SELECT id FROM po_lines WHERE po_id=?)",
                            (reason, po_id))
            self.db.execute("UPDATE purchase_orders SET status=?,note=TRIM(note||' '||?) WHERE id=?", (new, f"[{new}] {reason}", po_id))
        return self.po(po_id)

    def reschedule(self, delivery_id, new_date, reason):
        """Supplier changed the arrival date; A1 reads the new date through incoming()."""
        d = self._delivery(delivery_id)
        if d["status"] != "pending":
            raise PurchaseError("只能改期尚未到貨的批次")
        new_date = day(new_date, "新到貨日")
        with self.db:
            self.db.execute("UPDATE deliveries SET scheduled_date=?,note=? WHERE id=?", (new_date.isoformat(), text(reason, "改期原因"), delivery_id))

    def _delivery(self, delivery_id):
        return self._one("SELECT d.*,l.po_id,l.ingredient_id,l.pack_size,l.price_per_pack,o.po_no,o.status po_status,o.supplier_id,"
                         "i.unit,i.name ingredient_name,i.shelf_life_days FROM deliveries d JOIN po_lines l ON l.id=d.line_id "
                         "JOIN purchase_orders o ON o.id=l.po_id JOIN ingredients i ON i.id=l.ingredient_id WHERE d.id=?",
                         (delivery_id,), "到貨批次")

    def schedule(self, start=None, end=None):
        """Pending deliveries for the receiving dock, oldest first (overdue ones included)."""
        sql = ("SELECT d.id delivery_id,d.scheduled_date,d.packs,d.note,o.po_no,o.id po_id,s.name supplier_name,"
               "l.ingredient_id,i.name ingredient_name,l.pack_size,i.unit,l.price_per_pack FROM deliveries d "
               "JOIN po_lines l ON l.id=d.line_id JOIN purchase_orders o ON o.id=l.po_id JOIN suppliers s ON s.id=o.supplier_id "
               "JOIN ingredients i ON i.id=l.ingredient_id WHERE d.status='pending' AND o.status IN ('ordered','partial')")
        args = []
        if start:
            sql += " AND d.scheduled_date>=?"; args.append(day(start).isoformat())
        if end:
            sql += " AND d.scheduled_date<=?"; args.append(day(end).isoformat())
        rows = self._rows(sql + " ORDER BY d.scheduled_date,d.id", args)
        today = date.today().isoformat()
        for r in rows:
            r["qty"] = round(r["packs"] * r["pack_size"], 4)
            r["overdue"] = r["scheduled_date"] < today
        return rows

    # ---------- receiving inspection ----------
    def receive(self, delivery_id, received_packs, accepted_packs, expiry_date, inspector, checks=None,
                supplier_lot="", reject_reason="", price_per_pack=None, received_date=None, backorder_date=None, batch_code=None):
        """驗收: record the inspection, push accepted packs to B2 as one batch, update PO status.

        Short or rejected packs are closed unless backorder_date is given, which schedules a replacement delivery.
        """
        d = self._delivery(delivery_id)
        if d["po_status"] not in ("ordered", "partial"):
            raise PurchaseError("採購單尚未下單或已結案")
        if d["status"] != "pending":
            raise PurchaseError("此批次已驗收或取消")
        inspector = text(inspector, "驗收人")
        received = number(received_packs, "實收包數", integer=True)
        accepted = number(accepted_packs, "允收包數", integer=True)
        received_date = day(received_date, "驗收日") if received_date else date.today()
        if accepted > received:
            raise PurchaseError("允收包數不可大於實收包數")
        if accepted > d["packs"]:
            raise PurchaseError(f"允收包數不可超過本批應到 {d['packs']} 包，超收請退回")
        rejected = received - accepted
        reject_reason = text(reject_reason, "拒收原因", False)
        if rejected and not reject_reason:
            raise PurchaseError("有拒收時必須填寫原因")
        checks = checks or {}
        if not isinstance(checks, dict):
            raise PurchaseError("檢查項目格式錯誤")
        checks = {k: bool(checks.get(k, False)) for k in CHECKS}
        if accepted and not all(checks.values()):
            raise PurchaseError("檢查項目未全部通過時不可允收，請全數拒收並註明原因")
        expiry = None
        if accepted:
            expiry = day(expiry_date, "有效日期")
            if expiry <= received_date:
                raise PurchaseError("效期已到或已過，不可允收")
        price = d["price_per_pack"] if price_per_pack in (None, "") else number(price_per_pack, "實際每包單價")
        backorder = day(backorder_date, "補貨日") if backorder_date else None
        shortfall = d["packs"] - accepted
        if backorder and shortfall <= 0:
            raise PurchaseError("沒有短少或拒收，不需補貨")
        if backorder and backorder < received_date:
            raise PurchaseError("補貨日不可早於驗收日")
        batch_code = text(batch_code, "批號", False) or f"A2-{d['po_no']}-D{delivery_id}"
        # All validation is done before B2 commits, so a B2 batch never exists without its A2 receipt.
        b2_id = None
        if accepted and self.inventory is not None:
            b2_id = self.inventory.receive_batch(
                "ingredient", d["ingredient_id"], batch_code, expiry.isoformat(), accepted * d["pack_size"], d["unit"],
                price / d["pack_size"], inspector)
        with self.db:
            self.db.execute("INSERT INTO receipts(delivery_id,received_date,received_packs,accepted_packs,rejected_packs,reject_reason,"
                            "supplier_lot,batch_code,expiry_date,price_per_pack,checks_json,inspector,b2_batch_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (delivery_id, received_date.isoformat(), received, accepted, rejected, reject_reason,
                             text(supplier_lot, "供應商批號", False), batch_code if accepted else None,
                             expiry.isoformat() if expiry else None, price, json.dumps(checks), inspector, b2_id))
            self.db.execute("UPDATE deliveries SET status='received' WHERE id=?", (delivery_id,))
            if backorder:
                self.db.execute("INSERT INTO deliveries(line_id,scheduled_date,packs,status,note) VALUES(?,?,?,'pending',?)",
                                (d["line_id"], backorder.isoformat(), shortfall, f"補貨：D{delivery_id} 短少／拒收"))
            self._refresh_status(d["po_id"])
        return dict(delivery_id=delivery_id, accepted_packs=accepted, rejected_packs=rejected, shortfall=shortfall,
                    accepted_qty=round(accepted * d["pack_size"], 4), amount=round(accepted * price, 2),
                    price_variance=round((price - d["price_per_pack"]) * accepted, 2), batch_code=batch_code if accepted else None,
                    b2_batch_id=b2_id, backorder_date=backorder.isoformat() if backorder else None, po=self.po(d["po_id"]))

    def _refresh_status(self, po_id):
        states = {r[0] for r in self.db.execute("SELECT d.status FROM deliveries d JOIN po_lines l ON l.id=d.line_id WHERE l.po_id=?", (po_id,))}
        status = "partial" if "pending" in states else "received"
        self.db.execute("UPDATE purchase_orders SET status=? WHERE id=?", (status, po_id))

    def receipts(self, limit=200):
        rows = self._rows("SELECT r.*,d.scheduled_date,o.po_no,s.name supplier_name,l.ingredient_id,i.name ingredient_name,l.pack_size,i.unit,"
                          "l.price_per_pack po_price FROM receipts r JOIN deliveries d ON d.id=r.delivery_id JOIN po_lines l ON l.id=d.line_id "
                          "JOIN purchase_orders o ON o.id=l.po_id JOIN suppliers s ON s.id=o.supplier_id JOIN ingredients i ON i.id=l.ingredient_id "
                          "ORDER BY r.id DESC LIMIT ?", (limit,))
        for r in rows:
            r["checks"] = json.loads(r.pop("checks_json"))
            r["amount"] = round(r["accepted_packs"] * r["price_per_pack"], 2)
        return rows

    # ---------- outputs to other groups ----------
    def incoming(self):
        """A1 `incoming` rows: confirmed, not yet received, qty in base units, expiry estimated from shelf life."""
        out = []
        for r in self.schedule():
            shelf = self.db.execute("SELECT shelf_life_days FROM ingredients WHERE id=?", (r["ingredient_id"],)).fetchone()[0]
            out.append(dict(ingredient_id=r["ingredient_id"], qty=r["qty"], arrival_date=r["scheduled_date"],
                            expires_on=(day(r["scheduled_date"]) + timedelta(days=shelf)).isoformat(), status="confirmed",
                            po_no=r["po_no"], delivery_id=r["delivery_id"]))
        return out

    def a1_ingredients(self):
        """A1 `ingredients` rows. pack_size/lead_days come from the cheapest active supplier offer."""
        out = []
        for i in self.ingredients():
            offers = self._rows("SELECT p.pack_size,s.lead_days,p.price_per_pack FROM supplier_prices p JOIN suppliers s ON s.id=p.supplier_id "
                                "WHERE p.ingredient_id=? AND s.active=1 ORDER BY p.price_per_pack/p.pack_size,s.lead_days LIMIT 1", (i["id"],))
            if offers:
                out.append(dict(id=i["id"], name=i["name"], unit=i["unit"], pack_size=offers[0]["pack_size"],
                                lead_days=offers[0]["lead_days"], capacity=i["capacity"]))
        return out

    def spend_report(self, start=None, end=None):
        """C2 進貨支出 and supplier scorecard. Amount counts accepted packs at the invoiced price."""
        rows = self.receipts(limit=-1)
        if start:
            rows = [r for r in rows if r["received_date"] >= day(start).isoformat()]
        if end:
            rows = [r for r in rows if r["received_date"] <= day(end).isoformat()]
        suppliers = {}
        for r in rows:
            s = suppliers.setdefault(r["supplier_name"], dict(supplier_name=r["supplier_name"], deliveries=0, on_time=0,
                                                              received_packs=0, accepted_packs=0, amount=0.0, price_variance=0.0))
            s["deliveries"] += 1
            s["on_time"] += r["received_date"] <= r["scheduled_date"]
            s["received_packs"] += r["received_packs"]
            s["accepted_packs"] += r["accepted_packs"]
            s["amount"] += r["amount"]
            s["price_variance"] += (r["price_per_pack"] - r["po_price"]) * r["accepted_packs"]
        for s in suppliers.values():
            s["on_time_rate"] = round(s["on_time"] / s["deliveries"], 4)
            s["acceptance_rate"] = round(s["accepted_packs"] / s["received_packs"], 4) if s["received_packs"] else None
            s["amount"], s["price_variance"] = round(s["amount"], 2), round(s["price_variance"], 2)
        return dict(start=start, end=end, total_amount=round(sum(r["amount"] for r in rows), 2),
                    suppliers=sorted(suppliers.values(), key=lambda s: -s["amount"]),
                    records=[dict(receipt_id=r["id"], date=r["received_date"], supplier=r["supplier_name"], po_no=r["po_no"],
                                  ingredient_id=r["ingredient_id"], qty=round(r["accepted_packs"] * r["pack_size"], 4), unit=r["unit"],
                                  amount=r["amount"]) for r in rows])


def seed(service, today=None):
    """Clearly-labelled demo data matching A1's sample ingredient ids (base unit g)."""
    today = today or date.today()
    for args in [("rice", "生米", "g", 180, 200000), ("chicken", "雞腿肉", "g", 3, 60000),
                 ("vegetable", "蔬菜", "g", 3, 40000), ("tofu", "豆腐", "g", 4, 30000)]:
        service.upsert_ingredient(*args)
    a = service.add_supplier("（模擬）大盛米行", 2, 1000, "王老闆", "02-0000-0001")
    b = service.add_supplier("（模擬）鮮雞肉品", 1, 2000, "陳小姐", "02-0000-0002")
    c = service.add_supplier("（模擬）果菜批發", 1, 500, "林先生", "02-0000-0003")
    d = service.add_supplier("（模擬）綜合食材", 3, 3000, "張經理", "02-0000-0004")
    for sid, iid, pack, tiers in [
        (a, "rice", 5000, [(1, 230), (10, 210), (40, 195)]),
        (d, "rice", 30000, [(1, 1200), (5, 1110)]),
        (b, "chicken", 1000, [(1, 165), (20, 150), (60, 138)]),
        (d, "chicken", 2000, [(10, 300), (30, 285)]),
        (c, "vegetable", 1000, [(1, 45), (20, 38)]),
        (d, "vegetable", 5000, [(4, 200)]),
        (c, "tofu", 600, [(1, 28), (30, 24)]),
        (d, "tofu", 600, [(20, 25)]),
    ]:
        for min_packs, price in tiers:
            service.set_price(sid, iid, pack, min_packs, price)
