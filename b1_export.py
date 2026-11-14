"""B1:給 C2 的成本資料 + 整合 B2 的報廢與盤點(找出食譜以外的損耗)。"""
import os

from b1_db import init_b1_tables
from b1_production import produce, load_demo_stock
from b1_report import variance_report, print_report
from b2_inventory import InventoryService


def _period(column, start, end):
    """回傳 (SQL 條件, 參數),用 YYYY-MM-DD 篩選某個時間欄位(含頭尾兩天)。"""
    sql, args = "", []
    if start:
        sql += f" AND substr({column},1,10)>=?"; args.append(start)
    if end:
        sql += f" AND substr({column},1,10)<=?"; args.append(end)
    return sql, args


def ingredient_leakage(s, start=None, end=None):
    """原料的報廢與盤點短少(這些損耗不在食譜裡,是真正的浪費)。"""
    cond, args = _period("m.created_at", start, end)
    rows = s.db.execute(f"""
        SELECT b.item_id AS material, m.movement_type AS kind,
               SUM(-m.quantity_change) AS qty,
               SUM(-m.quantity_change * b.unit_cost) AS loss
        FROM inventory_movements m
        JOIN inventory_batches b ON b.id = m.batch_id
        WHERE b.item_type='ingredient' AND m.movement_type IN ('waste','stocktake'){cond}
        GROUP BY b.item_id, m.movement_type
        ORDER BY b.item_id""", args).fetchall()
    return [dict(r) for r in rows]


def bento_waste(s, start=None, end=None):
    """各便當的產出份數、報廢份數、報廢率與損失。"""
    pcond, pargs = _period("produced_at", start, end)
    wcond, wargs = _period("m.created_at", start, end)
    rows = s.db.execute(f"""
        SELECT p.product_code, p.produced,
               COALESCE(w.wasted,0) AS wasted, COALESCE(w.loss,0) AS loss
        FROM (SELECT product_code, SUM(actual_qty) AS produced
              FROM ProductionBatch WHERE 1=1{pcond} GROUP BY product_code) p
        LEFT JOIN (
            SELECT b.item_id, SUM(-m.quantity_change) AS wasted,
                   SUM(-m.quantity_change * b.unit_cost) AS loss
            FROM inventory_movements m
            JOIN inventory_batches b ON b.id = m.batch_id
            WHERE b.item_type='bento' AND m.movement_type='waste'{wcond}
            GROUP BY b.item_id) w ON w.item_id = p.product_code""", pargs + wargs).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["waste_rate"] = d["wasted"] / d["produced"] if d["produced"] else 0
        out.append(d)
    return out


def cost_summary_for_c2(s, start=None, end=None):
    """C2 經營報表直接呼叫這個函式,拿食材成本與損失。start、end 可按日期(例如按月)篩選。"""
    pcond, pargs = _period("produced_at", start, end)
    prod = s.db.execute(
        "SELECT COALESCE(SUM(total_cost),0) AS c, COALESCE(SUM(actual_qty),0) AS q "
        "FROM ProductionBatch WHERE 1=1" + pcond, pargs).fetchone()
    leak = ingredient_leakage(s, start, end)
    bento = bento_waste(s, start, end)
    leak_loss = sum(r["loss"] for r in leak)
    bento_loss = sum(r["loss"] for r in bento)
    wasted = sum(r["wasted"] for r in bento)
    good = prod["q"] - wasted
    return {
        "start": start, "end": end,
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


def print_c2_summary(s, start=None, end=None):
    r = cost_summary_for_c2(s, start, end)
    print(f"\n=== 食材成本與損耗摘要(給 C2,期間 {start or '不限'} ~ {end or '不限'})===")
    print(f"生產食材成本:{r['production_cost']:.2f}(共 {r['produced_qty']:g} 份,每份 {r['avg_unit_cost']:.2f})")
    print("--- 原料損耗(食譜以外的浪費)---")
    if not r["leakage"]:
        print("無")
    for l in r["leakage"]:
        kind = "報廢" if l["kind"] == "waste" else "盤點短少"
        print(f"  {l['material']:<10}{kind:<6}{l['qty']:>10.1f} g  損失 {l['loss']:.2f}")
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
    load_demo_stock(s)

    res = produce(s, "chicken_bento", 10, "BENTO-DEMO-001", planned_qty=11)
    print_report(variance_report(s, res["production_batch_id"]))

    # 模擬 B2 的盤點與報廢
    ids = {b["batch_code"]: b["id"] for b in s.batches()}
    s.waste(ids["CHICKEN-DEMO-A"], 300, "受潮", "B2")       # 原料報廢 300 g
    s.stocktake(ids["RICE-DEMO"], 18800, "B2")             # 生米盤點少 200 g
    s.waste(ids["BENTO-DEMO-001"], 1, "過期", "B2")        # 便當報廢 1 份

    print_c2_summary(s)
    s.close()
