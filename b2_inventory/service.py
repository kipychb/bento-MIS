"""B2 inventory, expiry, stocktake and waste management (SQLite, standard library)."""
import sqlite3
import math
from datetime import date, timedelta

class InventoryError(ValueError):
    pass

class InventoryService:
    def __init__(self, path="bento.db"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS inventory_batches (
          id INTEGER PRIMARY KEY, item_type TEXT NOT NULL CHECK(item_type IN ('ingredient','bento')),
          item_id TEXT NOT NULL, batch_code TEXT NOT NULL UNIQUE,
          expiry_date TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity>=0),
          unit TEXT NOT NULL, unit_cost REAL NOT NULL CHECK(unit_cost>=0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS inventory_movements (
          id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES inventory_batches(id),
          movement_type TEXT NOT NULL, quantity_change REAL NOT NULL,
          reason TEXT, operator TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stocktakes (
          id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES inventory_batches(id),
          expected_qty REAL NOT NULL, counted_qty REAL NOT NULL CHECK(counted_qty>=0),
          difference REAL NOT NULL, operator TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS waste_records (
          id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES inventory_batches(id),
          quantity REAL NOT NULL CHECK(quantity>0), unit_cost REAL NOT NULL,
          loss REAL NOT NULL, reason TEXT NOT NULL, operator TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def receive_batch(self, item_type, item_id, batch_code, expiry_date, quantity, unit, unit_cost, operator, input_unit=None, package_size=None, package_unit=None):
        if item_type not in ("ingredient", "bento") or not item_id or not batch_code or not unit or not operator:
            raise InventoryError("invalid batch fields")
        try:
            date.fromisoformat(expiry_date)
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
        with self.db:
            cur = self.db.execute(
                "INSERT INTO inventory_batches(item_type,item_id,batch_code,expiry_date,quantity,unit,unit_cost) VALUES(?,?,?,?,?,?,?)",
                (item_type,item_id,batch_code,expiry_date,quantity,unit,unit_cost))
            self._movement(cur.lastrowid, "receive", quantity, "received", operator)
        return cur.lastrowid

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
            "SELECT * FROM inventory_batches WHERE quantity>0 AND expiry_date<=? ORDER BY expiry_date,id",(cutoff,))]
    
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

    def waste_report(self):
        rows=self.db.execute("SELECT batch_id,quantity,loss,reason,operator,created_at FROM waste_records ORDER BY id DESC").fetchall()
        return {"total_loss":round(sum(r["loss"] for r in rows),2),"records":[dict(r) for r in rows]}
