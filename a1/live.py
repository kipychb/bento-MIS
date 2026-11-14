"""A1 系統資料:從共用資料庫與 A2 組出 A1 計算需要的輸入,取代手動匯入 JSON。

- products   ← B1 品項主檔與食譜(用量含損耗,跟 B1 實際領料一致)
- ingredients← A2 最便宜報價的包裝量與交期;沒有報價時用共用主檔與預設值
- sales      ← C1 各日、各時段現場銷量(不含預訂取餐)
- preorders  ← C1 預訂
- stock      ← B2 仍有庫存的原料批次
- incoming   ← A2 已下單、尚未驗收的到貨批次
"""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from shared.config import INVENTORY_DB, SLOTS  # noqa: E402
from shared.master import INGREDIENTS, ensure_master, products_for_a1  # noqa: E402

A2_DB = ROOT.parent / "a2" / "data" / "a2.sqlite3"
DEFAULT_LEAD_DAYS = 1


def _a2(a2_db):
    """讀 A2 的原料採購條件與在途;A2 尚未啟用時回傳空資料。"""
    if not Path(a2_db).exists():
        return {}, []
    sys.path.insert(0, str(ROOT.parent / "a2"))
    try:
        from service import PurchasingService  # a2/service.py
    finally:
        sys.path.pop(0)
    s = PurchasingService(str(a2_db))
    try:
        return {i["id"]: i for i in s.a1_ingredients()}, s.incoming()
    finally:
        s.close()


def live_data(inventory_db=INVENTORY_DB, a2_db=A2_DB, target=None):
    conn = sqlite3.connect(inventory_db)
    try:
        ensure_master(conn)
        products = products_for_a1(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sales, preorders = [], []
        if "c1_sales" in tables:
            sales = [dict(date=r[0], product_id=r[1], slot=r[2], walk_in_qty=int(r[3])) for r in conn.execute(
                "SELECT s.date,i.product_code,s.slot,SUM(i.qty) FROM c1_sales s JOIN c1_sale_items i ON i.sale_id=s.id "
                "WHERE s.source IN ('pos','history') GROUP BY s.date,i.product_code,s.slot ORDER BY s.date")]
            status = {"預訂中": "confirmed", "已取餐": "fulfilled", "已取消": "cancelled"}
            preorders = [dict(date=r[0], product_id=r[1], slot=r[2], qty=int(r[3]), status=status[r[4]]) for r in conn.execute(
                "SELECT r.pickup_date,i.product_code,r.slot,SUM(i.qty),r.status FROM c1_reservations r "
                "JOIN c1_reservation_items i ON i.reservation_id=r.id GROUP BY r.id,i.product_code")]
        stock = []
        if "inventory_batches" in tables:
            for item_id, qty, expiry in conn.execute(
                    "SELECT item_id,quantity,expiry_date FROM inventory_batches WHERE item_type='ingredient' AND quantity>0"):
                # B2 的效期是「當天還能用」;A1 的 expires_on 是「當天起不能用」,所以加一天
                expires_on = (date.fromisoformat(expiry[:10]) + timedelta(days=1)).isoformat()
                stock.append(dict(ingredient_id=item_id, qty=round(qty, 4), reserved_qty=0, expires_on=expires_on))
    finally:
        conn.close()

    offers, incoming = _a2(a2_db)
    ingredients = []
    for i in INGREDIENTS:
        o = offers.get(i["code"])
        ingredients.append(dict(id=i["code"], name=i["name"], unit=i["unit"],
                                pack_size=o["pack_size"] if o else 1000,
                                lead_days=o["lead_days"] if o else DEFAULT_LEAD_DAYS,
                                capacity=i["capacity"]))
    known = {i["id"] for i in ingredients}
    product_ids = {p["id"] for p in products}
    target = target or (date.today() + timedelta(days=1)).isoformat()
    return dict(target=target, factor=1, buffer=0.1, data=dict(
        products=products, ingredients=ingredients, slots=SLOTS,
        sales=[s for s in sales if s["product_id"] in product_ids and s["slot"] in SLOTS],
        preorders=[p for p in preorders if p["product_id"] in product_ids],
        stock=[s for s in stock if s["ingredient_id"] in known],
        incoming=[dict(ingredient_id=r["ingredient_id"], qty=r["qty"], arrival_date=r["arrival_date"],
                       expires_on=r["expires_on"], status="confirmed") for r in incoming if r["ingredient_id"] in known]))
