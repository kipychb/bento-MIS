"""B1 文字選單:把食譜、進貨、生產、差異報表、C2 摘要串在一起。"""
from datetime import date, datetime, timedelta

from b1_db import init_b1_tables
from b1_production import add_item, add_recipe, produce
from b1_report import init_standard_price, set_standard_price, variance_report, print_report
from b1_export import print_c2_summary
from b2_inventory import InventoryService


def ask(msg):
    return input(msg).strip()


def get_item(s, code):
    return s.db.execute("SELECT * FROM Item WHERE code=?", (code,)).fetchone()


def load_demo(s):
    add_item(s, "rice", "白飯", "ingredient", "kg")
    add_item(s, "chicken", "雞腿肉", "ingredient", "kg")
    add_item(s, "chicken_bento", "雞腿便當", "bento", "份", 6)
    add_recipe(s, "chicken_bento", "rice", 0.3)
    add_recipe(s, "chicken_bento", "chicken", 0.25, 0.1)
    set_standard_price(s, "rice", 40)
    set_standard_price(s, "chicken", 110)
    d = date.today()
    tag = d.strftime("%Y%m%d")
    s.receive_batch("ingredient", "rice", f"RICE-{tag}", (d + timedelta(days=30)).isoformat(), 20, "kg", 40, "B1")
    s.receive_batch("ingredient", "chicken", f"CHICKEN-{tag}-A", (d + timedelta(days=10)).isoformat(), 10, "kg", 120, "B1")
    s.receive_batch("ingredient", "chicken", f"CHICKEN-{tag}-B", (d + timedelta(days=2)).isoformat(), 2, "kg", 100, "B1")
    print("示範資料已載入。接著可選 5 生產,產品代碼輸入 chicken_bento。")


def menu_add_item(s):
    code = ask("品項代碼(英文,如 rice):")
    name = ask("品項名稱(如 白飯):")
    kind = ask("類型 1=原料 2=便當:")
    unit = ask("固定庫存單位(如 kg、份):")
    hours = None
    if kind == "2":
        hours = int(ask("保存時數(如 6):"))
    add_item(s, code, name, "bento" if kind == "2" else "ingredient", unit, hours)
    print("已新增品項。")


def menu_add_recipe(s):
    product = ask("便當代碼:")
    material = ask("原料代碼:")
    qty = float(ask("每份用量(單位同原料庫存單位):"))
    loss = float(ask("損耗率(0.1 = 10%,沒有就輸入 0):") or 0)
    add_recipe(s, product, material, qty, loss)
    print("已儲存食譜。")


def menu_std_price(s):
    code = ask("原料代碼:")
    price = float(ask("標準單價(每個庫存單位多少元):"))
    set_standard_price(s, code, price)
    print("已設定標準單價。")


def menu_receive(s):
    code = ask("原料代碼:")
    item = get_item(s, code)
    if item is None or item["type"] != "ingredient":
        print("找不到這個原料,請先用 1 新增品項。")
        return
    batch = ask("批次編號(不可重複):")
    days = int(ask("幾天後到期:"))
    qty = float(ask(f"數量({item['unit']}):"))
    cost = float(ask(f"每 {item['unit']} 多少元:"))
    expiry = (date.today() + timedelta(days=days)).isoformat()
    s.receive_batch("ingredient", code, batch, expiry, qty, item["unit"], cost, "B1")
    print("進貨完成。")


def menu_produce(s):
    code = ask("便當代碼:")
    qty = float(ask("實際產出幾份:"))
    out = f"{code.upper()}-{datetime.now():%Y%m%d-%H%M%S}"
    r = produce(s, code, qty, out)
    print(f"生產完成,批號 {out}:總成本 {r['total_cost']},每份 {r['unit_cost']}")


def menu_variance(s):
    pid = ask("生產批次 ID(直接按 Enter = 最新一批):")
    if not pid:
        row = s.db.execute("SELECT MAX(batch_id) AS m FROM ProductionBatch").fetchone()
        pid = row["m"]
        if pid is None:
            print("還沒有生產紀錄。")
            return
    print_report(variance_report(s, int(pid)))


def menu_stock(s):
    for b in s.batches():
        kind = "原料" if b["item_type"] == "ingredient" else "便當"
        print(f"{kind} {b['item_id']:<14}{b['batch_code']:<26}"
              f"{b['quantity']:>9.3f} {b['unit']}  到期 {b['expiry_date']}  單價 {b['unit_cost']:.2f}")


MENU = """
===== B1 食譜與生產系統 =====
1 新增品項      2 新增/修改食譜   3 設定標準單價
4 原料進貨      5 生產便當        6 差異報表
7 C2 成本與損耗摘要               8 查看庫存
9 載入示範資料                    0 離開
"""

ACTIONS = {"1": menu_add_item, "2": menu_add_recipe, "3": menu_std_price,
           "4": menu_receive, "5": menu_produce, "6": menu_variance,
           "7": print_c2_summary, "8": menu_stock, "9": load_demo}

if __name__ == "__main__":
    s = InventoryService("bento.db")
    init_b1_tables(s.db)
    init_standard_price(s)
    while True:
        print(MENU)
        choice = ask("請輸入數字:")
        if choice == "0":
            break
        action = ACTIONS.get(choice)
        if action is None:
            print("沒有這個選項。")
            continue
        try:
            action(s)
        except Exception as e:  # 輸入錯誤不要讓程式當掉
            print("操作失敗:", e)
    s.close()
    print("再見!")
