"""B1:標準成本 vs 實際成本差異報表。"""
import os
from datetime import date, timedelta

from b1_db import init_b1_tables
from b1_production import add_item, add_recipe, produce
from b2_inventory import InventoryService


def init_standard_price(s):
    s.db.execute("""
    CREATE TABLE IF NOT EXISTS StandardPrice (
        material_code TEXT PRIMARY KEY,
        std_price REAL NOT NULL   -- 每個庫存單位的標準單價
    )""")
    s.db.commit()


def set_standard_price(s, material_code, std_price):
    with s.db:
        s.db.execute(
            "INSERT OR REPLACE INTO StandardPrice(material_code,std_price) VALUES(?,?)",
            (material_code, std_price))


def variance_report(s, production_batch_id):
    """回傳一批生產的標準成本、實際成本、差異(正數=超支,負數=省錢)。"""
    conn = s.db
    pb = conn.execute("SELECT * FROM ProductionBatch WHERE batch_id=?",
                      (production_batch_id,)).fetchone()
    if pb is None:
        raise ValueError("找不到這個生產批次")
    recipe = conn.execute("SELECT * FROM Recipe WHERE product_code=?",
                          (pb["product_code"],)).fetchall()
    lines = []
    for r in recipe:
        sp = conn.execute("SELECT std_price FROM StandardPrice WHERE material_code=?",
                          (r["material_code"],)).fetchone()
        if sp is None:
            raise ValueError(f"原料 {r['material_code']} 還沒設標準單價")
        std_price = sp["std_price"]
        std_qty = r["qty_per_unit"] * pb["actual_qty"] / (1 - r["loss_rate"])
        u = conn.execute(
            "SELECT COALESCE(SUM(qty_used),0) q, COALESCE(SUM(qty_used*unit_cost),0) c "
            "FROM BatchMaterialUsage WHERE production_batch_id=? AND material_code=?",
            (production_batch_id, r["material_code"])).fetchone()
        act_qty, act_cost = u["q"], u["c"]
        std_cost = std_qty * std_price
        lines.append({
            "material": r["material_code"],
            "std_qty": std_qty, "act_qty": act_qty,
            "std_cost": std_cost, "act_cost": act_cost,
            "qty_var": (act_qty - std_qty) * std_price,   # 用量差異:用太多或太少
            "price_var": act_cost - act_qty * std_price,  # 價格差異:買貴或買便宜
            "total_var": act_cost - std_cost,
        })
    std_total = sum(l["std_cost"] for l in lines)
    act_total = sum(l["act_cost"] for l in lines)
    n = pb["actual_qty"]
    return {"batch": pb["output_batch_code"], "qty": n, "lines": lines,
            "std_total": std_total, "act_total": act_total,
            "var_total": act_total - std_total,
            "std_unit": std_total / n, "act_unit": act_total / n}


def print_report(r):
    print(f"\n=== 批次 {r['batch']}(產出 {r['qty']:g} 份)===")
    print(f"{'原料':<10}{'標準量':>8}{'實際量':>8}{'標準成本':>10}{'實際成本':>10}{'用量差異':>10}{'價格差異':>10}")
    for l in r["lines"]:
        print(f"{l['material']:<10}{l['std_qty']:>8.3f}{l['act_qty']:>8.3f}"
              f"{l['std_cost']:>10.2f}{l['act_cost']:>10.2f}"
              f"{l['qty_var']:>10.2f}{l['price_var']:>10.2f}")
    print(f"標準成本 {r['std_total']:.2f}(每份 {r['std_unit']:.2f})")
    print(f"實際成本 {r['act_total']:.2f}(每份 {r['act_unit']:.2f})")
    v = r["var_total"]
    print(f"差異 {v:+.2f}  →  " + ("超支(不利)" if v > 0 else "省錢(有利)" if v < 0 else "剛好"))


if __name__ == "__main__":
    if os.path.exists("b1_report_demo.db"):
        os.remove("b1_report_demo.db")
    s = InventoryService("b1_report_demo.db")
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
    s.close()
