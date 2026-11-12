"""B1:給 C2 的成本資料 + 整合 B2 的報廢與盤點(找出食譜以外的損耗)。"""
import os
from datetime import date, timedelta

from b1_db import init_b1_tables
from b1_production import add_item, add_recipe, produce
from b1_report import init_standard_price, set_standard_price, variance_report, print_report
from b2_inventory import InventoryService


def ingredient_leakage(s):
    """原料的報廢與盤點短少(這些損耗不在食譜裡,是真正的浪費)。"""
    rows = s.db.execute("""
        SELECT b.item_id AS material, m.movement_type AS kind,
               SUM(-m.quantity_change) AS qty,
               SUM(-m.quantity_change * b.unit_cost) AS loss
        FROM inventory_movements m
        JOIN inventory_batches b ON b.id = m.batch_id
        WHERE b.item_type='ingredient' AND m.movement_type IN ('waste','stocktake')
        GROUP BY b.item_id, m.movement_type
        ORDER BY b.item_id""").fetchall()
    return [dict(r) for r in rows]


def bento_waste(s):
    """各便當的產出份數、報廢份數、報廢率與損失。"""
    rows = s.db.execute("""
        SELECT p.product_code, p.produced,
               COALESCE(w.wasted,0) AS wasted, COALESCE(w.loss,0) AS loss
        FROM (SELECT product_code, SUM(actual_qty) AS produced
              FROM ProductionBatch GROUP BY product_code) p
        LEFT JOIN (
            SELECT b.item_id, SUM(-m.quantity_change) AS wasted,
                   SUM(-m.quantity_change * b.unit_cost) AS loss
            FROM inventory_movements m
            JOIN inventory_batches b ON b.id = m.batch_id
            WHERE b.item_type='bento' AND m.movement_type='waste'
            GROUP BY b.item_id) w ON w.item_id = p.product_code""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["waste_rate"] = d["wasted"] / d["produced"] if d["produced"] else 0
        out.append(d)
    return out


def cost_summary_for_c2(s):
    """C2 經營報表直接呼叫這個函式,拿食材成本與損失。"""
    prod = s.db.execute(
        "SELECT COALESCE(SUM(total_cost),0) AS c, COALESCE(SUM(actual_qty),0) AS q "
        "FROM ProductionBatch").fetchone()
    leak = ingredient_leakage(s)
    bento = bento_waste(s)
    leak_loss = sum(r["loss"] for r in leak)
    bento_loss = sum(r["loss"] for r in bento)
    wasted = sum(r["wasted"] for r in bento)
    good = prod["q"] - wasted
    return {
        "production_cost": round(prod["c"], 2),   # 生產用掉的食材成本
        "produced_qty": prod["q"],
        "ingredient_loss": round(leak_loss, 2),   # 原料報廢+盤點短少(額外損失)
        "bento_waste_loss": round(bento_loss, 2), # 便當報廢(已含在生產成本內)
        "bento_wasted_qty": wasted,
        "waste_rate": wasted / prod["q"] if prod["q"] else 0,
        "avg_unit_cost": round(prod["c"] / prod["q"], 2) if prod["q"] else 0,
        # 把額外損失都算進去,再除以真正賣得出去的份數
        "effective_unit_cost": round((prod["c"] + leak_loss) / good, 2) if good > 0 else 0,
        "leakage": leak,
        "bento": bento,
    }


def print_c2_summary(s):
    r = cost_summary_for_c2(s)
    print("\n=== 食材成本與損耗摘要(給 C2)===")
    print(f"生產食材成本:{r['production_cost']:.2f}(共 {r['produced_qty']:g} 份,每份 {r['avg_unit_cost']:.2f})")
    print("--- 原料損耗(食譜以外的浪費)---")
    if not r["leakage"]:
        print("無")
    for l in r["leakage"]:
        kind = "報廢" if l["kind"] == "waste" else "盤點短少"
        print(f"  {l['material']:<10}{kind:<6}{l['qty']:>8.3f}  損失 {l['loss']:.2f}")
    print(f"原料額外損失合計:{r['ingredient_loss']:.2f}")
    print("--- 便當報廢 ---")
    for b in r["bento"]:
        flag = "  ⚠ 超過 3% 目標" if b["waste_rate"] > 0.03 else ""
        print(f"  {b['product_code']}:產出 {b['produced']:g},報廢 {b['wasted']:g},"
              f"報廢率 {b['waste_rate']:.1%},損失 {b['loss']:.2f}{flag}")
    print(f"每份有效成本(含所有損失、扣除報廢):{r['effective_unit_cost']:.2f}")


if __name__ == "__main__":
    if os.path.exists("b1_export_demo.db"):
        os.remove("b1_export_demo.db")
    s = InventoryService("b1_export_demo.db")
    init_b1_tables(s.db)
    init_standard_price(s)

    add_item(s, "rice", "白飯", "ingredient", "kg")
    add_item(s, "chicken", "雞腿肉", "ingredient", "kg")
    add_item(s, "chicken_bento", "雞腿便當", "bento", "份", 6)
    add_recipe(s, "chicken_bento", "rice", 0.3)
    add_recipe(s, "chicken_bento", "chicken", 0.25, 0.1)
    set_standard_price(s, "rice", 40)
    set_standard_price(s, "chicken", 110)

    d = date.today()
    s.receive_batch("ingredient", "rice", "RICE-001", (d + timedelta(days=30)).isoformat(), 20, "kg", 40, "A2")
    s.receive_batch("ingredient", "chicken", "CHICKEN-001", (d + timedelta(days=10)).isoformat(), 10, "kg", 120, "A2")
    s.receive_batch("ingredient", "chicken", "CHICKEN-000", (d + timedelta(days=2)).isoformat(), 2, "kg", 100, "A2")

    res = produce(s, "chicken_bento", 10, "BENTO-DEMO-001", planned_qty=11)
    print_report(variance_report(s, res["production_batch_id"]))

    # 模擬 B2 的盤點與報廢
    ids = {b["batch_code"]: b["id"] for b in s.batches()}
    s.waste(ids["CHICKEN-001"], 0.3, "受潮", "B2")        # 原料報廢 0.3 kg
    s.stocktake(ids["RICE-001"], 16.8, "B2")              # 白飯盤點少 0.2 kg
    s.waste(ids["BENTO-DEMO-001"], 1, "過期", "B2")       # 便當報廢 1 份

    print_c2_summary(s)
    s.close()
