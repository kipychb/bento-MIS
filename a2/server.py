"""Local A2 API: suppliers, POs, split deliveries, receiving -> B2 stock-in. Python standard library only."""
import argparse
import json
import os
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
sys.path.append(str(ROOT.parent))  # import B2 from the repo root
from b2_inventory import InventoryService  # noqa: E402
from service import PurchasingService, PurchaseError, seed  # noqa: E402

DB = Path(os.environ.get("A2_DB_PATH", ROOT / "data" / "a2.sqlite3"))
B2_DB = os.environ.get("B2_DB_PATH", str(ROOT.parent / "b2_local.db"))  # same file b2_app.py and C2 use


def open_service():
    DB.parent.mkdir(parents=True, exist_ok=True)
    return PurchasingService(str(DB), InventoryService(B2_DB) if B2_DB else None)


def close_service(s):
    if s.inventory is not None:
        s.inventory.close()
    s.close()


def q(query, key, default=None):
    return parse_qs(query).get(key, [default])[0]


GET = [
    (r"/api/a2/ingredients", lambda s, m, qs: s.ingredients()),
    (r"/api/a2/suppliers", lambda s, m, qs: s.suppliers()),
    (r"/api/a2/pos", lambda s, m, qs: s.pos(q(qs, "status"))),
    (r"/api/a2/pos/(\d+)", lambda s, m, qs: s.po(int(m[1]))),
    (r"/api/a2/schedule", lambda s, m, qs: s.schedule(q(qs, "start"), q(qs, "end"))),
    (r"/api/a2/receipts", lambda s, m, qs: s.receipts()),
    (r"/api/a2/incoming", lambda s, m, qs: s.incoming()),
    (r"/api/a2/a1-ingredients", lambda s, m, qs: s.a1_ingredients()),
    (r"/api/a2/spend", lambda s, m, qs: s.spend_report(q(qs, "start"), q(qs, "end"))),
    (r"/api/a2/compare", lambda s, m, qs: s.compare(q(qs, "ingredient_id"), float(q(qs, "qty", "nan")), q(qs, "need_by"), q(qs, "order_date"))),
]

POST = [
    (r"/api/a2/ingredients", lambda s, m, b: s.upsert_ingredient(b.get("id"), b.get("name"), b.get("unit"), b.get("shelf_life_days"), b.get("capacity"))),
    (r"/api/a2/suppliers", lambda s, m, b: s.add_supplier(b.get("name"), b.get("lead_days"), b.get("min_order_amount", 0), b.get("contact", ""), b.get("phone", ""))),
    (r"/api/a2/suppliers/(\d+)/active", lambda s, m, b: s.set_supplier_active(int(m[1]), b.get("active"))),
    (r"/api/a2/prices", lambda s, m, b: s.set_price(b.get("supplier_id"), b.get("ingredient_id"), b.get("pack_size"), b.get("min_packs"), b.get("price_per_pack"))),
    (r"/api/a2/prices/(\d+)/delete", lambda s, m, b: s.delete_price(int(m[1]))),
    (r"/api/a2/pos", lambda s, m, b: s.create_po(b.get("supplier_id"), b.get("lines"), b.get("created_by"), b.get("order_date"), b.get("note", ""), b.get("source", "manual"))),
    (r"/api/a2/pos/(\d+)/confirm", lambda s, m, b: s.confirm_po(int(m[1]))),
    (r"/api/a2/pos/(\d+)/cancel", lambda s, m, b: s.cancel_po(int(m[1]), b.get("reason"))),
    (r"/api/a2/deliveries/(\d+)/receive", lambda s, m, b: s.receive(
        int(m[1]), b.get("received_packs"), b.get("accepted_packs"), b.get("expiry_date"), b.get("inspector"), b.get("checks"),
        b.get("supplier_lot", ""), b.get("reject_reason", ""), b.get("price_per_pack"), b.get("received_date"), b.get("backorder_date"), b.get("batch_code"))),
    (r"/api/a2/deliveries/(\d+)/reschedule", lambda s, m, b: s.reschedule(int(m[1]), b.get("date"), b.get("reason"))),
    (r"/api/a2/split", lambda s, m, b: s.split_plan(b.get("packs"), b.get("first_date"), b.get("max_packs"), b.get("interval_days", 1))),
    (r"/api/a2/a1-suggestions", lambda s, m, b: s.suggestions_from_a1(b.get("result", b), b.get("order_date"))),
]


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, mime="application/json; charset=utf-8"):
        raw = json.dumps(body, ensure_ascii=False).encode() if mime.startswith("application/json") else body
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def route(self, table, arg):
        url = urlparse(self.path)
        for pattern, fn in table:
            m = re.fullmatch(pattern, url.path)
            if m:
                s = open_service()
                try:
                    return fn(s, m, url.query if arg is None else arg)
                finally:
                    close_service(s)
        raise LookupError

    def handle_api(self, table, arg=None):
        try:
            return self.send(200, {"result": self.route(table, arg)})
        except LookupError:
            return self.send(404, {"error": "找不到 API"})
        except sqlite3.IntegrityError as e:
            return self.send(400, {"error": "資料重複或關聯錯誤：" + str(e)})
        except (ValueError, TypeError, KeyError, AttributeError) as e:  # PurchaseError / B2 InventoryError are ValueError
            return self.send(400, {"error": str(e)})

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/a2"):
            return self.send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        return self.handle_api(GET)

    def do_POST(self):
        # Block browser requests originating on another website.
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            return self.send(403, {"error": "不允許跨網站請求"})
        if self.path == "/api/a2/seed":
            s = open_service()
            try:
                if s.suppliers():
                    return self.send(400, {"error": "已有供應商資料，不重複載入模擬資料"})
                seed(s)
                return self.send(200, {"result": "ok"})
            finally:
                close_service(s)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 2000000:
                raise ValueError
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.send(400, {"error": "請求必須是 2 MB 以內的 JSON 物件"})
        return self.handle_api(POST, body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()
    print(f"A2 供應商／採購／驗收：http://127.0.0.1:{args.port}  （B2 資料庫：{B2_DB}）", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
