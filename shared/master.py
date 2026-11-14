"""全系統唯一的品項主檔。

- 品項代號：A1、A2、B1、B2、C1、C2 全部使用這裡的 code。
- 單位：原料一律以「g」為庫存單位，便當以「份」為單位。
- 食譜：每份便當的生料用量（g），loss_rate 是前處理損耗。

正式資料存在共用資料庫的 B1 Item / Recipe 表；這個檔案只負責第一次建立。
"""

INGREDIENTS = [
    # code, 名稱, 單位, 保存天數, 倉儲容量(g)
    dict(code="rice", name="生米", unit="g", shelf_life_days=180, capacity=200000),
    dict(code="chicken", name="雞腿肉", unit="g", shelf_life_days=3, capacity=60000),
    dict(code="vegetable", name="蔬菜", unit="g", shelf_life_days=3, capacity=40000),
    dict(code="tofu", name="豆腐", unit="g", shelf_life_days=4, capacity=30000),
]

BENTOS = [
    # made_to_order=True：先預訂、後製作（巨無霸便當不預先做，避免報廢）
    dict(code="chicken_bento", name="雞腿便當", price=75, shelf_life_hours=6, made_to_order=False,
         recipe=[("rice", 100, 0), ("chicken", 200, 0.05), ("vegetable", 150, 0.1)]),
    dict(code="tofu_bento", name="豆腐便當", price=60, shelf_life_hours=6, made_to_order=False,
         recipe=[("rice", 100, 0), ("tofu", 180, 0), ("vegetable", 150, 0.1)]),
    dict(code="giant_1kg", name="1公斤巨無霸便當", price=150, shelf_life_hours=6, made_to_order=True,
         recipe=[("rice", 250, 0), ("chicken", 450, 0.05), ("vegetable", 300, 0.1)]),
    dict(code="giant_3kg", name="3公斤大胃王便當", price=400, shelf_life_hours=6, made_to_order=True,
         recipe=[("rice", 750, 0), ("chicken", 1350, 0.05), ("vegetable", 900, 0.1)]),
]

# B1 標準單價（每 g 元），用於標準成本與差異分析
STANDARD_PRICES = {"rice": 0.042, "chicken": 0.15, "vegetable": 0.04, "tofu": 0.042}


def ensure_master(conn):
    """把主檔寫進共用資料庫的 B1 Item / Recipe 表。已存在的品項不覆蓋。"""
    from b1_db import init_b1_tables

    init_b1_tables(conn)
    with conn:
        for i in INGREDIENTS:
            conn.execute("INSERT OR IGNORE INTO Item(code,name,type,unit) VALUES(?,?,'ingredient',?)",
                         (i["code"], i["name"], i["unit"]))
        for b in BENTOS:
            conn.execute("INSERT OR IGNORE INTO Item(code,name,type,unit,shelf_life_hours,price,made_to_order) "
                         "VALUES(?,?,'bento','份',?,?,?)",
                         (b["code"], b["name"], b["shelf_life_hours"], b["price"], int(b["made_to_order"])))
            for material, qty, loss in b["recipe"]:
                conn.execute("INSERT OR IGNORE INTO Recipe(product_code,material_code,qty_per_unit,loss_rate) VALUES(?,?,?,?)",
                             (b["code"], material, qty, loss))


def products_for_a1(conn):
    """A1 的 products：食譜用量含損耗，與 B1 生產實際領料一致。"""
    out = []
    for item in conn.execute("SELECT code,name,made_to_order FROM Item WHERE type='bento' ORDER BY item_id").fetchall():
        recipe = {r[0]: round(r[1] / (1 - r[2]), 4) for r in conn.execute(
            "SELECT material_code,qty_per_unit,loss_rate FROM Recipe WHERE product_code=?", (item[0],))}
        if recipe:
            out.append(dict(id=item[0], name=item[1], recipe=recipe, made_to_order=bool(item[2])))
    return out
