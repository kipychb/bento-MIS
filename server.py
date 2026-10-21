from flask import Flask, request, jsonify, send_from_directory
import os
from service import C1POSService

app = Flask(__name__)
service = C1POSService()

@app.route("/")
def index():
    return send_from_directory(os.path.dirname(__file__), "index.html")

# 取得菜單與目前庫存
@app.route("/api/c1/menu", methods=["GET"])
def get_menu():
    return jsonify(service.get_menu())

# POS 現場結帳 API
@app.route("/api/c1/checkout", methods=["POST"])
def checkout():
    data = request.json
    items = data.get("items", [])
    payment_method = data.get("payment_method", "現金")
    result = service.pos_checkout(items, payment_method)
    status_code = 200 if result["success"] else 400
    return jsonify(result), status_code

# 取得所有預訂與新增預訂 API
@app.route("/api/c1/reservations", methods=["GET", "POST"])
def handle_reservations():
    if request.method == "GET":
        return jsonify(service.reservations)
    data = request.json
    result = service.create_reservation(
        customer_name=data.get("customer_name"),
        phone=data.get("phone"),
        pickup_time=data.get("pickup_time"),
        items=data.get("items", []),
        note=data.get("note", "")
    )
    return jsonify(result), (200 if result["success"] else 400)

# 更新預訂狀態（取餐 / 取消）API
@app.route("/api/c1/reservations/<res_id>", methods=["PATCH"])
def update_reservation(res_id):
    data = request.json
    new_status = data.get("status")
    result = service.update_reservation_status(res_id, new_status)
    return jsonify(result), (200 if result["success"] else 400)

# 顧客回饋 API
@app.route("/api/c1/feedbacks", methods=["GET", "POST"])
def handle_feedbacks():
    if request.method == "GET":
        return jsonify(service.feedbacks)
    data = request.json
    result = service.add_feedback(
        customer_name=data.get("customer_name"),
        rating=data.get("rating", 5),
        comment=data.get("comment", "")
    )
    return jsonify(result)

# 提供給 C2 (經營報表) 與 A1 (需求預測) 抓取銷售數據的整合 API
@app.route("/api/c1/export", methods=["GET"])
def export_data():
    return jsonify(service.get_summary_for_c2_and_a1())

if __name__ == "__main__":
    print("🚀 C1 POS 與顧客管理系統已啟動：http://127.0.0.1:5001")
    app.run(port=5001, debug=True)