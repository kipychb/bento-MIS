"""C1 POS 與顧客管理 API(Flask)。資料存在共用資料庫 bento.db。

啟動:pip install flask  →  python c1/server.py  →  http://127.0.0.1:5001
"""
import argparse
import os
import sys
from contextlib import contextmanager

from flask import Flask, request, jsonify, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service import C1POSService  # noqa: E402
from shared.config import INVENTORY_DB  # noqa: E402

app = Flask(__name__)
app.config["DB_PATH"] = INVENTORY_DB


@contextmanager
def service():
    # SQLite 連線不能跨執行緒共用,所以每個請求各開一次
    s = C1POSService(app.config["DB_PATH"])
    try:
        yield s
    finally:
        s.close()


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@app.route("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


# 取得菜單與目前庫存(庫存即 B2 中未過期的便當批次)
@app.route("/api/c1/menu", methods=["GET"])
def get_menu():
    with service() as s:
        return jsonify(s.get_menu())


# POS 現場結帳 API
@app.route("/api/c1/checkout", methods=["POST"])
def checkout():
    data = body()
    with service() as s:
        result = s.pos_checkout(data.get("items", []), data.get("payment_method", "現金"))
    return jsonify(result), (200 if result["success"] else 400)


# 取得所有預訂與新增預訂 API
@app.route("/api/c1/reservations", methods=["GET", "POST"])
def handle_reservations():
    with service() as s:
        if request.method == "GET":
            return jsonify(s.reservations)
        data = body()
        result = s.create_reservation(
            customer_name=data.get("customer_name"),
            phone=data.get("phone"),
            pickup_time=data.get("pickup_time"),
            items=data.get("items", []),
            note=data.get("note", ""),
            pickup_date=data.get("pickup_date"),
        )
    return jsonify(result), (200 if result["success"] else 400)


# 更新預訂狀態(取餐 / 取消)API
@app.route("/api/c1/reservations/<res_id>", methods=["PATCH"])
def update_reservation(res_id):
    with service() as s:
        result = s.update_reservation_status(res_id, body().get("status"))
    return jsonify(result), (200 if result["success"] else 400)


# 顧客回饋 API
@app.route("/api/c1/feedbacks", methods=["GET", "POST"])
def handle_feedbacks():
    with service() as s:
        if request.method == "GET":
            return jsonify(s.feedbacks)
        data = body()
        result = s.add_feedback(data.get("customer_name"), data.get("rating", 5), data.get("comment", ""))
    return jsonify(result), (200 if result["success"] else 400)


# C1 頁面用的營收摘要與回饋
@app.route("/api/c1/export", methods=["GET"])
def export_data():
    with service() as s:
        return jsonify(s.get_summary_for_c2_and_a1())


# 給 C2:已完成銷售(含每份食材成本),可用 ?start=&end= 篩選
@app.route("/api/c1/sales", methods=["GET"])
def sales_for_c2():
    with service() as s:
        return jsonify(s.sales_for_c2(request.args.get("start"), request.args.get("end")))


# 給 A1:各時段現場銷量與預訂
@app.route("/api/c1/a1", methods=["GET"])
def for_a1():
    with service() as s:
        return jsonify({"sales": s.walk_in_for_a1(), "preorders": s.preorders_for_a1()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    print(f"C1 POS 與顧客管理系統已啟動:http://127.0.0.1:{args.port}(資料庫 {app.config['DB_PATH']})")
    app.run(host="127.0.0.1", port=args.port, debug=False)
