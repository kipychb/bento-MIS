"""B2 inventory, expiry, stocktake and waste management (SQLite, standard library).

所有模組的庫存異動都經過這裡：A2 驗收入庫、B1 生產領料與成品入庫、C1 銷售出庫。
效期可以是日期（YYYY-MM-DD，當天結束前可用）或時間（YYYY-MM-DDTHH:MM:SS，便當 6 小時效期）。
"""
import sqlite3
import math
from datetime import date, datetime, timedelta

class InventoryError(ValueError):
    pass

NOW = "datetime('now','localtime')"
# 效期比較：只有日期的批次，視為當天 23:59:59 到期
EXPIRY_END = "(CASE WHEN length(expiry_date)=10 THEN expiry_date||'T23:59:59' ELSE expiry_date END)"


def normalize_expiry(value):
    """接受 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM[:SS]，回傳標準字串。"""
    if not isinstance(value, str):
        raise InventoryError("invalid date or number")
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        return datetime.fromisoformat(value.replace(" ", "T")).replace(microsecond=0).isoformat()
    except ValueError:
        raise InventoryError("invalid date or number")


def now_iso(now=None):
    return (now or datetime.now()).replace(microsecond=0).isoformat()


class InventoryService:
    def __init__(self, path="bento.db"):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(f"""
        CREATE TABLE IF NOT EXISTS inventory_batches (
          id INTEGER PRIMARY KEY, item_type TEXT NOT NULL CHECK(item_type IN ('ingredient','bento')),
          item_id TEXT NOT NULL, batch_code TEXT NOT NULL UNIQUE,
          expiry_date TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity>=0),
          unit TEXT NOT NULL, unit_cost REAL NOT NULL CHECK(unit_cost>=0),
          created_at TEXT NOT NULL DEFAULT ({NOW})
        );
        CREATE TABLE IF NOT EXISTS inventory_movements (
          id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES inventory_batches(id),
          movement_type TEXT NOT NULL, quantity_change REAL NOT NULL,
          reason TEXT, operator TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT ({NOW})
        );
        CREATE TABLE IF NOT EXISTS stocktakes (
          id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES inventory_batches(id),
          expected_qty REAL NOT NULL, counted_qty REAL NOT NULL CHECK(counted_qty>=0),
          difference REAL NOT NULL, operator TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT ({NOW})
        );
        CREATE TABLE IF NOT EXISTS waste_records (
          id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES inventory_batches(id),
          quantity REAL NOT NULL CHECK(quantity>0), unit_cost REAL NOT NULL,
          loss REAL NOT NULL, reason TEXT NOT NULL, operator TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT ({NOW})
        );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def receive_batch(self, item_type, item_id, batch_code, expiry_date, quantity, unit, unit_cost, operator, input_unit=None, package_size=None, package_unit=None):
        if item_type not in ("ingredient", "bento") or not item_id or not batch_code or not unit or not operator:
            raise InventoryError("invalid batch fields")
        expiry_date = normalize_expiry(expiry_date)
        try:
            quantity, unit_cost = float(quantity), float(unit_cost)
        except (ValueError, TypeError):
            raise InventoryError("invalid date or number")
        if not math.isfinite(quantity) or not math.isfinite(unit_cost) or quantity < 0 or unit_cost < 0:
            raise InventoryError("quantity and cost must be finite and nonnegative")
        unit = unit.strip()
        if unit not in {"g", "kg", "mL", "L", "顆", "份", "包"}:
            raise InventoryError("unsupported stock unit")
        input_unit = (input_unit or unit).strip()
        conversions = {("g", "kg"): .001, ("kg", "g"): 1000,
                       ("mL", "L"): .001, ("L", "mL"): 1000}
        if input_unit == "包" and unit != "包":
            if package_size is None or not package_unit:
                raise InventoryError("每包規格必填，例如 200 g")
            try:
                package_size = float(package_size)
            except (TypeError, ValueError):
                raise InventoryError("invalid package size")
            if not math.isfinite(package_size) or package_size <= 0:
                raise InventoryError("package size must be positive")
            factor = conversions.get((package_unit, unit), 1 if package_unit == unit else None)
            if factor is None:
                raise InventoryError("每包規格與庫存單位不相容")
            conversion = package_size * factor
        else:
            conversion = conversions.get((input_unit, unit), 1 if input_unit == unit else None)
            if conversion is None:
                raise InventoryError("輸入單位與庫存單位不相容")
        # Price is per entered unit; store cost per normalized inventory unit.
        quantity = round(quantity * conversion, 6)
        unit_cost = round(unit_cost / conversion, 8)
        previous = self.db.execute(
            "SELECT DISTINCT unit FROM inventory_batches WHERE item_type=? AND item_id=?",
            (item_type, item_id)).fetchall()
        if any(row["unit"] != unit for row in previous):
            raise InventoryError("此品項已有固定庫存單位，不能混用不同單位")
        master_unit = self._master_unit(item_type, item_id)
        if master_unit and master_unit != unit:
            raise InventoryError(f"品項 {item_id} 在主檔的庫存單位是 {master_unit}，請改用 {master_unit}")
        with self.db:
            cur = self.db.execute(
                "INSERT INTO inventory_batches(item_type,item_id,batch_code,expiry_date,quantity,unit,unit_cost) VALUES(?,?,?,?,?,?,?)",
                (item_type,item_id,batch_code,expiry_date,quantity,unit,unit_cost))
            self._movement(cur.lastrowid, "receive", quantity, "received", operator)
        return cur.lastrowid

    def _master_unit(self, item_type, item_id):
        """共用資料庫有 B1 品項主檔時，入庫單位必須跟主檔一致。"""
        has_item = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='Item'").fetchone()
        if not has_item:
            return None
        row = self.db.execute("SELECT unit FROM Item WHERE code=? AND type=?", (item_id, item_type)).fetchone()
        return row["unit"] if row else None

    def _movement(self, batch_id, kind, change, reason, operator):
        self.db.execute("INSERT INTO inventory_movements(batch_id,movement_type,quantity_change,reason,operator) VALUES(?,?,?,?,?)",
                        (batch_id,kind,change,reason,operator))

    def batches(self, item_type=None):
        if item_type is not None and item_type not in ("ingredient", "bento"):
            raise InventoryError("invalid item type")
        sql="SELECT * FROM inventory_batches"
        rows=self.db.execute(sql+(" WHERE item_type=?" if item_type else "")+" ORDER BY expiry_date,id",
                             (item_type,) if item_type else ()).fetchall()
        return [dict(row) for row in rows]

    def expiry_alerts(self, days=3, today=None):
        if not isinstance(days,int) or days<0:
            raise InventoryError("days must be nonnegative integer")
        today = today or date.today()
        cutoff=(today+timedelta(days=days)).isoformat()
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM inventory_batches WHERE quantity>0 AND substr(expiry_date,1,10)<=? ORDER BY expiry_date,id",(cutoff,))]

    # ---------- 出庫（FEFO：先到期先出，已過期不出） ----------
    def available(self, item_type, item_id, now=None):
        """目前還沒過期、可以使用的數量。"""
        row = self.db.execute(
            f"SELECT COALESCE(SUM(quantity),0) FROM inventory_batches WHERE item_type=? AND item_id=? AND quantity>0 AND {EXPIRY_END}>=?",
            (item_type, item_id, now_iso(now))).fetchone()
        return round(row[0], 6)

    def plan_consumption(self, item_type, item_id, quantity, now=None):
        """依 FEFO 規劃要從哪些批次扣多少；不足時丟出錯誤，不會改資料。"""
        try:
            need = float(quantity)
        except (TypeError, ValueError):
            raise InventoryError("invalid quantity")
        if not math.isfinite(need) or need <= 0:
            raise InventoryError("quantity must be positive")
        rows = self.db.execute(
            f"SELECT * FROM inventory_batches WHERE item_type=? AND item_id=? AND quantity>0 AND {EXPIRY_END}>=? ORDER BY {EXPIRY_END},id",
            (item_type, item_id, now_iso(now))).fetchall()
        plan = []
        for row in rows:
            if need <= 1e-9:
                break
            take = min(need, row["quantity"])
            plan.append(dict(batch_id=row["id"], batch_code=row["batch_code"], item_id=item_id,
                             quantity=round(take, 6), unit_cost=row["unit_cost"]))
            need -= take
        if need > 1e-6:
            raise InventoryError(f"{item_id} 庫存不足，還缺 {need:g}")
        return plan

    def apply_consumption(self, plan, reason, operator):
        """執行 plan_consumption 的結果。不會自己 commit，由呼叫端放在同一個交易裡。"""
        if not operator:
            raise InventoryError("operator required")
        for line in plan:
            cur = self.db.execute("UPDATE inventory_batches SET quantity=quantity-? WHERE id=? AND quantity>=?",
                                  (line["quantity"], line["batch_id"], line["quantity"] - 1e-9))
            if cur.rowcount != 1:
                raise InventoryError("庫存已被其他操作變動，請重試")
            self._movement(line["batch_id"], "consume", -line["quantity"], reason, operator)

    def consume(self, item_type, item_id, quantity, reason, operator, now=None):
        """單一品項出庫（FEFO），回傳扣了哪些批次。"""
        plan = self.plan_consumption(item_type, item_id, quantity, now)
        with self.db:
            self.apply_consumption(plan, reason, operator)
        return plan

    def _batch(self, batch_id):
        row=self.db.execute("SELECT * FROM inventory_batches WHERE id=?",(batch_id,)).fetchone()
        if row is None:
            raise InventoryError("batch not found")
        return row

    def stocktake(self, batch_id, counted_qty, operator):
        if not operator:
            raise InventoryError("operator required")
        try: counted_qty=float(counted_qty)
        except (TypeError,ValueError): raise InventoryError("invalid count")
        if counted_qty<0: raise InventoryError("negative count")
        with self.db:
            row=self._batch(batch_id)
            expected=float(row["quantity"])
            diff=counted_qty-expected
            cur=self.db.execute("INSERT INTO stocktakes(batch_id,expected_qty,counted_qty,difference,operator) VALUES(?,?,?,?,?)",
                                (batch_id,expected,counted_qty,diff,operator))
            self.db.execute("UPDATE inventory_batches SET quantity=? WHERE id=?",(counted_qty,batch_id))
            self._movement(batch_id,"stocktake",diff,"stocktake adjustment",operator)
        return {"id":cur.lastrowid,"expected":expected,"counted":counted_qty,"difference":diff}

    def waste(self, batch_id, quantity, reason, operator):
        if not reason or not operator: raise InventoryError("reason and operator required")
        try: quantity=float(quantity)
        except (TypeError,ValueError): raise InventoryError("invalid quantity")
        if quantity<=0: raise InventoryError("quantity must be positive")
        with self.db:
            row=self._batch(batch_id)
            if quantity>row["quantity"]: raise InventoryError("insufficient stock")
            loss=round(quantity*row["unit_cost"],2)
            self.db.execute("UPDATE inventory_batches SET quantity=quantity-? WHERE id=?",(quantity,batch_id))
            cur=self.db.execute("INSERT INTO waste_records(batch_id,quantity,unit_cost,loss,reason,operator) VALUES(?,?,?,?,?,?)",
                                (batch_id,quantity,row["unit_cost"],loss,reason,operator))
            self._movement(batch_id,"waste",-quantity,reason,operator)
        return {"id":cur.lastrowid,"loss":loss}

    def waste_report(self, start=None, end=None):
        """報廢彙總；start、end（YYYY-MM-DD，含當天）可篩選期間，用於按月統計。"""
        where, args = [], []
        if start:
            where.append("substr(w.created_at,1,10)>=?"); args.append(date.fromisoformat(start).isoformat())
        if end:
            where.append("substr(w.created_at,1,10)<=?"); args.append(date.fromisoformat(end).isoformat())
        rows=self.db.execute(
            "SELECT w.id,w.batch_id,b.item_type,b.item_id,w.quantity,w.loss,w.reason,w.operator,w.created_at "
            "FROM waste_records w JOIN inventory_batches b ON b.id=w.batch_id"
            + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY w.id DESC", args).fetchall()
        return {"start":start,"end":end,"total_loss":round(sum(r["loss"] for r in rows),2),"records":[dict(r) for r in rows]}
