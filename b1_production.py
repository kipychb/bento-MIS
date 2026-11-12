"""B1:食譜、分批生產、FEFO 領料、批次成本。使用 B2 的 InventoryService。"""
import os
from datetime import date, datetime, timedelta

from b1_db import init_b1_tables
from b2_inventory import InventoryService


def add_item(s, code, name, item_type, unit, shelf_life_hours=None):
    with s.db:
        s.db.execute(
            "INSERT OR IGNORE INTO Item(code,name,type,unit,shelf_life_hours) VALUES(?,?,?,?,?)",
            (code, name, item_type, unit, shelf_life_hours))


def add_recipe(s, product_code, material_code, qty_per_unit, loss_rate=0):
    with s.db:
        s.db.execute(
            "INSERT OR REPLACE INTO Recipe(product_code,material_code,qty_per_unit,loss_rate) VALUES(?,?,?,?)",
            (product_code, material_code, qty_per_unit, loss_rate))


def produce(s, product_code, actual_qty, output_batch_code, operator="B1", planned_qty=None):
    """生產一批便當:FEFO 扣原料、算成本、成品入庫。全部成功或全部不做。"""
    conn = s.db
    product = conn.execute(
        "SELECT * FROM Item WHERE code=? AND type='bento'", (product_code,)).fetchone()
    if product is None:
        raise ValueError("找不到這個便當品項")
    if actual_qty <= 0:
        raise ValueError("產出數量必須大於 0")
    recipe = conn.execute("SELECT * FROM Recipe WHERE product_code=?", (product_code,)).fetchall()
    if not recipe:
        raise ValueError("這個便當還沒有食譜")
    units = conn.execute(
        "SELECT DISTINCT unit FROM inventory_batches WHERE item_type='bento' AND item_id=?",
        (product_code,)).fetchall()
    if any(u["unit"] != product["unit"] for u in units):
        raise ValueError("成品單位跟 B2 庫存裡的單位不一致")

    now = datetime.now()
    today = now.date().isoformat()

    # 1. 先規劃要從哪些批次扣多少(FEFO:先到期先用,已過期的不用)
    plan = []  # (原料代碼, 批次資料, 扣多少)
    for r in recipe:
        need = r["qty_per_unit"] * actual_qty / (1 - r["loss_rate"])
        batches = conn.execute(
            "SELECT * FROM inventory_batches WHERE item_type='ingredient' AND item_id=? "
            "AND quantity>0 AND expiry_date>=? ORDER BY expiry_date,id",
            (r["material_code"], today)).fetchall()
        for b in batches:
            if need <= 1e-9:
                break
            take = min(need, b["quantity"])
            plan.append((r["material_code"], b, take))
            need -= take
        if need > 1e-6:
            raise ValueError(f"原料 {r['material_code']} 庫存不足,還缺 {need:.3f}")

    # 2. 算成本
    total_cost = round(sum(take * b["unit_cost"] for _, b, take in plan), 2)
    unit_cost = round(total_cost / actual_qty, 4)
    hours = product["shelf_life_hours"] or 24
    expiry = (now + timedelta(hours=hours)).date().isoformat()

    # 3. 一次寫入(中途出錯會整個復原)
    with conn:
        cur = conn.execute(
            "INSERT INTO ProductionBatch(product_code,output_batch_code,planned_qty,actual_qty,total_cost,unit_cost) "
            "VALUES(?,?,?,?,?,?)",
            (product_code, output_batch_code, planned_qty, actual_qty, total_cost, unit_cost))
        pb_id = cur.lastrowid
        for material_code, b, take in plan:
            conn.execute("UPDATE inventory_batches SET quantity=quantity-? WHERE id=?", (take, b["id"]))
            conn.execute(
                "INSERT INTO inventory_movements(batch_id,movement_type,quantity_change,reason,operator) "
                "VALUES(?,?,?,?,?)", (b["id"], "consume", -take, f"production {output_batch_code}", operator))
            conn.execute(
                "INSERT INTO BatchMaterialUsage(production_batch_id,material_code,material_batch_code,qty_used,unit_cost) "
                "VALUES(?,?,?,?,?)", (pb_id, material_code, b["batch_code"], take, b["unit_cost"]))
        cur2 = conn.execute(
            "INSERT INTO inventory_batches(item_type,item_id,batch_code,expiry_date,quantity,unit,unit_cost) "
            "VALUES('bento',?,?,?,?,?,?)",
            (product_code, output_batch_code, expiry, actual_qty, product["unit"], unit_cost))
        conn.execute(
            "INSERT INTO inventory_movements(batch_id,movement_type,quantity_change,reason,operator) "
            "VALUES(?,?,?,?,?)", (cur2.lastrowid, "receive", actual_qty, "production output", operator))
    return {"production_batch_id": pb_id, "total_cost": total_cost, "unit_cost": unit_cost}


if __name__ == "__main__":
    # 示範:用全新的資料庫跑一遍
    if os.path.exists("b1_demo.db"):
        os.remove("b1_demo.db")
    s = InventoryService("b1_demo.db")
    init_b1_tables(s.db)

    add_item(s, "rice", "白飯", "ingredient", "kg")
    add_item(s, "chicken", "雞腿肉", "ingredient", "kg")
    add_item(s, "chicken_bento", "雞腿便當", "bento", "份", 6)
    add_recipe(s, "chicken_bento", "rice", 0.3)
    add_recipe(s, "chicken_bento", "chicken", 0.25, 0.1)

    d = date.today()
    s.receive_batch("ingredient", "rice", "RICE-001", (d + timedelta(days=30)).isoformat(), 20, "kg", 40, "A2")
    s.receive_batch("ingredient", "chicken", "CHICKEN-001", (d + timedelta(days=10)).isoformat(), 10, "kg", 120, "A2")
    s.receive_batch("ingredient", "chicken", "CHICKEN-000", (d + timedelta(days=2)).isoformat(), 2, "kg", 100, "A2")

    print(produce(s, "chicken_bento", 10, "BENTO-DEMO-001", planned_qty=11))
    for b in s.batches():
        print(b["item_id"], b["batch_code"], round(b["quantity"], 3), b["unit"], round(b["unit_cost"], 2))
    s.close()
