"""Standalone local B2 web app. Python 3, no third-party dependencies."""
import json
import os
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from b2_inventory import InventoryService, InventoryError

ROOT = Path(__file__).resolve().parent
DB_PATH = os.environ.get("B2_DB_PATH", str(ROOT / "b2_local.db"))
HTML = (ROOT / "b2_inventory" / "index.html").read_bytes()

class Handler(BaseHTTPRequestHandler):
    def respond(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/b2"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(HTML)))
            self.end_headers()
            self.wfile.write(HTML)
            return
        if not url.path.startswith("/api/b2/"):
            return self.respond(404, {"error": "找不到路徑"})
        try:
            service = InventoryService(DB_PATH)
            try:
                if url.path == "/api/b2/batches":
                    result = service.batches()
                elif url.path == "/api/b2/alerts":
                    days = int(parse_qs(url.query).get("days", ["3"])[0])
                    result = service.expiry_alerts(days)
                    today = date.today().isoformat()
                    for batch in result:
                        batch["status"] = "已過期" if batch["expiry_date"] < today else ("今日到期" if batch["expiry_date"] == today else "即將到期")
                elif url.path == "/api/b2/waste":
                    result = service.waste_report()
                elif url.path == "/api/b2/movements":
                    result = [dict(r) for r in service.db.execute(
                        "SELECT m.*, b.batch_code, b.item_id FROM inventory_movements m JOIN inventory_batches b ON b.id=m.batch_id ORDER BY m.id DESC LIMIT 200")]
                elif url.path == "/api/b2/stocktakes":
                    result = [dict(r) for r in service.db.execute(
                        "SELECT s.*, b.batch_code FROM stocktakes s JOIN inventory_batches b ON b.id=s.batch_id ORDER BY s.id DESC LIMIT 200")]
                else:
                    return self.respond(404, {"error": "找不到路徑"})
            finally:
                service.close()
            return self.respond(200, result)
        except (InventoryError, ValueError) as exc:
            return self.respond(400, {"error": str(exc)})
        except Exception:
            return self.respond(500, {"error": "伺服器處理失敗"})

    def do_POST(self):
        paths = {
            "/api/b2/batches": ("receive_batch", ("item_type","item_id","batch_code","expiry_date","quantity","unit","unit_cost","operator")),
            "/api/b2/stocktake": ("stocktake", ("batch_id","counted_qty","operator")),
            "/api/b2/waste": ("waste", ("batch_id","quantity","reason","operator"))
        }
        if self.path not in paths:
            return self.respond(404, {"error": "找不到路徑"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 16384:
                return self.respond(413, {"error": "資料大小不正確"})
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("資料格式錯誤")
            name, fields = paths[self.path]
            service = InventoryService(DB_PATH)
            try:
                result = getattr(service, name)(**{k: data[k] for k in fields})
            finally:
                service.close()
            return self.respond(201, {"result": result})
        except (ValueError, TypeError, KeyError, InventoryError) as exc:
            return self.respond(400, {"error": str(exc)})
        except Exception:
            return self.respond(500, {"error": "伺服器處理失敗"})

if __name__ == "__main__":
    host = "127.0.0.1"
    port = int(os.environ.get("B2_PORT", "8000"))
    print(f"B2 管理系統啟動：http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
