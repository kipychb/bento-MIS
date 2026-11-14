"""一鍵示範:用各組真正的程式,把「採購 → 驗收入庫 → 生產 → 銷售 → 分析 → 預測」整個流程跑一遍。

    python demo.py           # 資料庫已有資料時會停下來,不會覆蓋
    python demo.py --reset   # 刪除本機示範資料庫後重新建立

建立的資料都標示「（模擬）」或「歷史匯入」,只寫入本機的資料庫檔:
  bento.db(共用)、a2/data/a2.sqlite3(A2)、c2/data/c2.sqlite3(C2)
跑完後啟動各模組的網頁,就能看到同一份資料。
"""
import argparse
import importlib.util
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from b1_export import cost_summary_for_c2  # noqa: E402
from b1_production import produce  # noqa: E402
from b1_report import init_standard_price, set_standard_price  # noqa: E402
from b2_inventory import InventoryService  # noqa: E402
from shared.config import INVENTORY_DB  # noqa: E402
from shared.master import BENTOS, INGREDIENTS, STANDARD_PRICES, ensure_master  # noqa: E402

A2_DB = ROOT / "a2" / "data" / "a2.sqlite3"
C2_DB = ROOT / "c2" / "data" / "c2.sqlite3"


def load(name, path):
    """各組都有一個叫 service.py 的檔案,用不同名稱載入以免互相覆蓋。"""
    sys.path.insert(0, str(Path(path).parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def run(inventory_db=INVENTORY_DB, a2_db=A2_DB, c2_db=C2_DB, now=None, quiet=False):
    say = (lambda *a: None) if quiet else print
    now = (now or datetime.now()).replace(microsecond=0)
    today = now.date()
    a2 = load("a2_service", ROOT / "a2" / "service.py")
    c1 = load("c1_service", ROOT / "c1" / "service.py")
    c2 = load("c2_service", ROOT / "c2" / "service.py")
    live = load("a1_live", ROOT / "a1" / "live.py")
    core = load("a1_core", ROOT / "a1" / "core.py")

    # 0. 共用主檔(B1 品項與食譜)
    inv = InventoryService(str(inventory_db))
    ensure_master(inv.db)
    init_standard_price(inv)
    for code, price in STANDARD_PRICES.items():
        set_standard_price(inv, code, price)
    say(f"[主檔] {len(BENTOS)} 種便當、{len(INGREDIENTS)} 種原料,原料單位一律 g")

    # 1. A2:供應商、比價、採購單、驗收入庫(寫進 B2)
    Path(a2_db).parent.mkdir(parents=True, exist_ok=True)
    purchasing = a2.PurchasingService(str(a2_db), inv)
    for i in INGREDIENTS:
        purchasing.upsert_ingredient(i["code"], i["name"], i["unit"], i["shelf_life_days"], i["capacity"])
    if not purchasing.suppliers():
        a2.seed(purchasing, today)
    def best_offer(code, qty, need_by=None):
        # A2 的排序:準時優先、總價最低;再排除未達供應商最低訂購金額的報價
        offers = purchasing.compare(code, qty, need_by, today.isoformat())["offers"]
        return next(o for o in offers if o["on_time"] and not any("最低訂購金額" in w for w in o["warnings"]))

    need = {"rice": 30000, "chicken": 30000, "vegetable": 24000, "tofu": 12000}
    received = 0
    for code, qty in need.items():
        best = best_offer(code, qty)
        order_day = today - timedelta(days=best["lead_days"])  # 依交期提早下單,今天到貨
        po = purchasing.create_po(best["supplier_id"], [dict(ingredient_id=code, packs=best["packs"], pack_size=best["pack_size"],
                                  deliveries=[dict(date=today.isoformat(), packs=best["packs"])])], "A2", order_day.isoformat(), source="A1")
        purchasing.confirm_po(po["id"])
        shelf = next(i["shelf_life_days"] for i in INGREDIENTS if i["code"] == code)
        for d in purchasing.po(po["id"])["lines"][0]["deliveries"]:
            purchasing.receive(d["id"], d["packs"], d["packs"], (today + timedelta(days=shelf)).isoformat(), "（模擬）驗收員",
                               checks=dict(temperature=True, package=True, appearance=True, label=True), received_date=today.isoformat())
            received += 1
    # 明天到貨、尚未驗收的一張單:A1 會把它算成「在途」
    best = best_offer("chicken", 15000)
    po = purchasing.create_po(best["supplier_id"], [dict(ingredient_id="chicken", packs=best["packs"], pack_size=best["pack_size"],
                              deliveries=[dict(date=(today + timedelta(days=1)).isoformat(), packs=best["packs"])])], "A2", today.isoformat(), source="A1")
    purchasing.confirm_po(po["id"])
    purchasing.close()
    say(f"[A2] 驗收入庫 {received} 批原料到 B2,另有 1 張明天到貨的雞腿肉採購單")

    # 2. B1:依今天的需求分批生產(FEFO 領料、成品入庫、6 小時效期)
    tag = now.strftime("%Y%m%d%H%M%S")
    plan = [("chicken_bento", 40), ("tofu_bento", 25), ("giant_1kg", 1)]
    for code, qty in plan:
        r = produce(inv, code, qty, f"{code.upper()}-{tag}", planned_qty=qty, now=now - timedelta(minutes=30))
        say(f"[B1] 生產 {code} {qty} 份,每份食材成本 {r['unit_cost']:.2f} 元,效期到 {r['expiry']}")

    # 3. C1:歷史銷售匯入(讓 A1 有資料可預測)、現場結帳、預訂、回饋
    pos = c1.C1POSService(str(inventory_db))
    history = []
    for back in range(28, 0, -1):
        d = today - timedelta(days=back)
        bump = d.weekday() % 3
        for code, base, cost in [("chicken_bento", 36, 42), ("tofu_bento", 22, 19)]:
            for t, ratio in [("12:10", 1.0), ("18:20", 0.8), ("21:40", 0.3)]:
                history.append(dict(date=d.isoformat(), local_time=t, product_code=code, qty=int(base * ratio) + bump, unit_cost=cost))
    pos.import_history(history)
    sold = 0
    for items, pay in [([{"id": "chicken_bento", "qty": 3}], "現金"), ([{"id": "tofu_bento", "qty": 2}, {"id": "chicken_bento", "qty": 1}], "LINE Pay"),
                       ([{"id": "chicken_bento", "qty": 5}], "現金"), ([{"id": "tofu_bento", "qty": 4}], "現金")]:
        r = pos.pos_checkout(items, pay, now=now)
        assert r["success"], r
        sold += sum(i["qty"] for i in items)
    giant = pos.create_reservation("（模擬）王同學", "0900-000-001", (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M"),
                                   [{"id": "giant_1kg", "qty": 1}], "飯要壓緊", now=now)
    pickup = pos.update_reservation_status(giant["reservation"]["res_id"], "已取餐", now=now)
    assert pickup["success"], pickup
    tomorrow = today + timedelta(days=1)
    pos.create_reservation("（模擬）大胃王社", "0900-000-002", f"{tomorrow}T12:00", [{"id": "giant_3kg", "qty": 2}], now=now)
    pos.create_reservation("（模擬）林老師", "0900-000-003", f"{tomorrow}T12:30", [{"id": "chicken_bento", "qty": 10}], "公司訂餐", now=now)
    pos.add_feedback("（模擬）阿明", 5, "份量真的很大,看直播知道剛炸好就來買", now=now)
    pos.add_feedback("（模擬）小美", 4, "豆腐便當希望多一點菜", now=now)
    pos.close()
    say(f"[C1] 匯入 28 天歷史銷售;今天現場賣出 {sold} 份,巨無霸預訂取餐 1 份;明天有 2 筆預訂")

    # 4. B2:盤點與報廢
    ids = {b["batch_code"]: b["id"] for b in inv.batches()}
    inv.waste(ids[f"CHICKEN_BENTO-{tag}"], 2, "（模擬）超過 6 小時未售出", "B2")
    veg = next(b for b in inv.batches("ingredient") if b["item_id"] == "vegetable" and b["quantity"] > 0)
    inv.stocktake(veg["id"], round(veg["quantity"] - 300, 3), "B2")
    say("[B2] 報廢雞腿便當 2 份;蔬菜盤點短少 300 g")

    # 5. C2:費用登錄,並從共用資料庫同步 C1 銷售
    for back in range(28, -1, -1):  # 跟歷史銷售同一段期間,每天登錄營業費用
        d = (today - timedelta(days=back)).isoformat()
        for category, amount, text in [("personnel", 2400, "（模擬）兼職人員 2 人"), ("utilities", 500, "（模擬）水電瓦斯日攤提"),
                                       ("rent", 800, "（模擬）租金日攤提"), ("other", 100, "（模擬）清潔用品")]:
            c2.save_expense(dict(id=f"DEMO-{d}-{category}", date=d, category=category, amount=amount, description=text), c2_db)
    synced = c2.sync_c1_sales(inventory_db, c2_db)
    summary = cost_summary_for_c2(inv, today.isoformat(), today.isoformat())
    waste = inv.waste_report(today.isoformat(), today.isoformat())
    inv.close()
    say(f"[C2] 同步 C1 銷售 {synced} 筆;今天生產食材成本 {summary['production_cost']:.0f} 元,報廢損失 {waste['total_loss']:.0f} 元")

    # 6. A1:讀系統資料,預測明天需求並算採購建議
    body = live.live_data(str(inventory_db), str(a2_db), tomorrow.isoformat())
    result = core.calculate(body["data"], body["target"], body["factor"], body["buffer"], today.isoformat())
    total = sum(f["total"] for f in result["forecasts"])
    buy = [p for p in result["purchases"] if p["purchase"] > 0]
    say(f"[A1] 明天建議製作 {total} 份;需採購 {len(buy)} 種原料:" + "、".join(f"{p['name']} {p['purchase']:g} g" for p in buy))
    return dict(received=received, sold=sold, synced=synced, summary=summary, waste=waste, a1=result, a1_input=body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="刪除本機示範資料庫後重新建立")
    args = parser.parse_args()
    files = [Path(INVENTORY_DB), A2_DB, C2_DB]
    if args.reset:
        for f in files:
            if f.exists():
                os.remove(f)
                print(f"已刪除 {f}")
    elif any(f.exists() for f in files):
        sys.exit("已有資料庫檔案,為避免覆蓋請改用:python demo.py --reset")
    run()
    print("\n完成。啟動各模組網頁即可看到同一份資料(見 README「怎麼啟動」)。")
