"""B1:食譜、分批生產、FEFO 領料、批次成本。庫存異動一律透過 B2 的 InventoryService。"""
import os
from datetime import date, datetime, timedelta

from b1_db import init_b1_tables
from b2_inventory import InventoryService, InventoryError
from shared.master import ensure_master, STANDARD_PRICES


def add_item(s, code, name, item_type, unit, shelf_life_hours=None, price=None, made_to_order=False):
    with s.db:
        s.db.execute(
            "INSERT OR IGNORE INTO Item(code,name,type,unit,shelf_life_hours,price,made_to_order) VALUES(?,?,?,?,?,?,?)",
            (code, name, item_type, unit, shelf_life_hours, price, int(bool(made_to_order))))


def add_recipe(s, product_code, material_code, qty_per_unit, loss_rate=0):
    if not 0 <= loss_rate < 1:
        raise ValueError("損耗率必須介於 0 與 1 之間")
    with s.db:
        s.db.execute(
            "INSERT OR REPLACE INTO Recipe(product_code,material_code,qty_per_unit,loss_rate) VALUES(?,?,?,?)",
            (product_code, material_code, qty_per_unit, loss_rate))


def produce(s, product_code, actual_qty, output_batch_code, operator="B1", planned_qty=None, now=None):
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

    now = now or datetime.now()

    # 1. 先請 B2 規劃要從哪些批次扣多少(FEFO:先到期先用,已過期的不用)
    plan = []
    for r in recipe:
        material = conn.execute("SELECT unit FROM Item WHERE code=?", (r["material_code"],)).fetchone()
        stock_units = {u[0] for u in conn.execute(
            "SELECT DISTINCT unit FROM inventory_batches WHERE item_type='ingredient' AND item_id=?", (r["material_code"],))}
        if material and stock_units and stock_units != {material["unit"]}:
            raise ValueError(f"原料 {r['material_code']} 的庫存單位跟主檔 {material['unit']} 不一致")
        need = r["qty_per_unit"] * actual_qty / (1 - r["loss_rate"])
        try:
            plan.extend(s.plan_consumption("ingredient", r["material_code"], need, now))
        except InventoryError as e:
            raise ValueError(f"原料 {r['material_code']} 庫存不足:{e}")

    # 2. 算成本
    total_cost = round(sum(p["quantity"] * p["unit_cost"] for p in plan), 2)
    unit_cost = round(total_cost / actual_qty, 4)
    hours = product["shelf_life_hours"] or 24
    expiry = (now + timedelta(hours=hours)).replace(microsecond=0).isoformat()  # 精確到時間,6 小時效期

    # 3. 一次寫入(中途出錯會整個復原)
    with conn:
        cur = conn.execute(
            "INSERT INTO ProductionBatch(product_code,output_batch_code,planned_qty,actual_qty,total_cost,unit_cost,produced_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (product_code, output_batch_code, planned_qty, actual_qty, total_cost, unit_cost,
             now.replace(microsecond=0).isoformat(sep=" ")))
        pb_id = cur.lastrowid
        s.apply_consumption(plan, f"production {output_batch_code}", operator)
        for p in plan:
            conn.execute(
                "INSERT INTO BatchMaterialUsage(production_batch_id,material_code,material_batch_code,qty_used,unit_cost) "
                "VALUES(?,?,?,?,?)", (pb_id, p["item_id"], p["batch_code"], p["quantity"], p["unit_cost"]))
        cur2 = conn.execute(
            "INSERT INTO inventory_batches(item_type,item_id,batch_code,expiry_date,quantity,unit,unit_cost) "
            "VALUES('bento',?,?,?,?,?,?)",
            (product_code, output_batch_code, expiry, actual_qty, product["unit"], unit_cost))
        conn.execute(
            "INSERT INTO inventory_movements(batch_id,movement_type,quantity_change,reason,operator) "
            "VALUES(?,?,?,?,?)", (cur2.lastrowid, "receive", actual_qty, "production output", operator))
    return {"production_batch_id": pb_id, "total_cost": total_cost, "unit_cost": unit_cost, "expiry": expiry}


def load_demo_stock(s, tag="DEMO"):
    """示範用:建立共用主檔,並以 g 為單位進三批原料(其中雞腿肉有一批較早到期)。"""
    from b1_report import init_standard_price, set_standard_price

    ensure_master(s.db)
    init_standard_price(s)
    for code, price in STANDARD_PRICES.items():
        set_standard_price(s, code, price)
    d = date.today()
    s.receive_batch("ingredient", "rice", f"RICE-{tag}", (d + timedelta(days=30)).isoformat(), 20000, "g", 0.04, "B1")
    s.receive_batch("ingredient", "chicken", f"CHICKEN-{tag}-A", (d + timedelta(days=3)).isoformat(), 10000, "g", 0.16, "B1")
    s.receive_batch("ingredient", "chicken", f"CHICKEN-{tag}-B", (d + timedelta(days=1)).isoformat(), 2000, "g", 0.14, "B1")
    s.receive_batch("ingredient", "vegetable", f"VEG-{tag}", (d + timedelta(days=2)).isoformat(), 8000, "g", 0.04, "B1")


if __name__ == "__main__":
    # 示範:用全新的資料庫跑一遍
    if os.path.exists("b1_demo.db"):
        os.remove("b1_demo.db")
    s = InventoryService("b1_demo.db")
    init_b1_tables(s.db)
    load_demo_stock(s)

    print(produce(s, "chicken_bento", 10, "BENTO-DEMO-001", planned_qty=11))
    for b in s.batches():
        print(b["item_id"], b["batch_code"], round(b["quantity"], 3), b["unit"], round(b["unit_cost"], 4), b["expiry_date"])
    s.close()
