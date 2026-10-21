from datetime import datetime

class C1POSService:
    def __init__(self):
        # 預設便當菜單與成品初始庫存（可與 B2 庫存系統同步）
        self.menu = {
            "B01": {"id": "B01", "name": "200日元超值漢堡排便當", "price": 50, "stock": 30, "is_giant": False},
            "B02": {"id": "B02", "name": "招牌酥脆炸雞便當", "price": 75, "stock": 25, "is_giant": False},
            "B03": {"id": "B03", "name": "1公斤巨無霸滿載便當", "price": 150, "stock": 10, "is_giant": True},
            "B04": {"id": "B04", "name": "3公斤大胃王專屬定製便當", "price": 400, "stock": 3, "is_giant": True},
        }
        self.sales_records = []   # 儲存 POS 交易紀錄（供 C2 營收報表與 A1 預測使用）
        self.reservations = []    # 儲存顧客預訂紀錄
        self.feedbacks = []       # 儲存顧客回饋

    def get_menu(self):
        return list(self.menu.values())

    def pos_checkout(self, items, payment_method="cash"):
        """
        POS 結帳功能：計算總額、記錄營收、扣除成品庫存
        items 格式: [{"id": "B01", "qty": 2}, ...]
        """
        total_amount = 0
        purchased_items = []

        # 1. 先檢查庫存是否足夠（防呆機制）
        for item in items:
            bento_id = item.get("id")
            qty = int(item.get("qty", 0))
            if bento_id not in self.menu:
                return {"success": False, "error": f"找不到品項 {bento_id}"}
            if self.menu[bento_id]["stock"] < qty:
                return {"success": False, "error": f"【{self.menu[bento_id]['name']}】成品庫存不足！（剩餘: {self.menu[bento_id]['stock']}）"}

        # 2. 扣除成品庫存並計算金額
        for item in items:
            bento_id = item.get("id")
            qty = int(item.get("qty", 0))
            bento = self.menu[bento_id]
            bento["stock"] -= qty
            subtotal = bento["price"] * qty
            total_amount += subtotal
            purchased_items.append({
                "id": bento_id,
                "name": bento["name"],
                "price": bento["price"],
                "qty": qty,
                "subtotal": subtotal
            })

        # 3. 建立銷售紀錄
        now = datetime.now()
        record = {
            "order_id": f"POS-{now.strftime('%Y%m%d%H%M%S')}-{len(self.sales_records)+1}",
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "hour_slot": now.strftime("%H:00"), # 提供給 C2/A1 分析各時段銷量
            "items": purchased_items,
            "total_amount": total_amount,
            "payment_method": payment_method,
            "source": "POS現場結帳"
        }
        self.sales_records.append(record)
        return {"success": True, "record": record}

    def create_reservation(self, customer_name, phone, pickup_time, items, note=""):
        """建立顧客預訂（特別針對巨無霸便當或大量預訂）"""
        total_amount = 0
        reserved_items = []

        for item in items:
            bento_id = item.get("id")
            qty = int(item.get("qty", 0))
            if bento_id not in self.menu:
                return {"success": False, "error": f"找不到品項 {bento_id}"}
            bento = self.menu[bento_id]
            total_amount += bento["price"] * qty
            reserved_items.append({
                "id": bento_id,
                "name": bento["name"],
                "price": bento["price"],
                "qty": qty
            })

        now = datetime.now()
        res = {
            "res_id": f"RES-{now.strftime('%m%d')}-{len(self.reservations)+1:03d}",
            "customer_name": customer_name,
            "phone": phone,
            "pickup_time": pickup_time,
            "items": reserved_items,
            "total_amount": total_amount,
            "status": "預訂中",  # 狀態：預訂中 / 已取餐 / 已取消
            "note": note,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.reservations.append(res)
        return {"success": True, "reservation": res}

    def update_reservation_status(self, res_id, new_status):
        """處理顧客取餐或取消預訂"""
        for res in self.reservations:
            if res["res_id"] == res_id:
                if res["status"] != "預訂中":
                    return {"success": False, "error": f"此訂單狀態已為「{res['status']}」，無法變更。"}

                if new_status == "已取餐":
                    # 取餐時自動轉為 POS 營收並扣除成品庫存
                    checkout_res = self.pos_checkout(res["items"], payment_method="預訂取餐付款")
                    if not checkout_res["success"]:
                        return checkout_res
                    res["status"] = "已取餐"
                    return {"success": True, "reservation": res, "sales_record": checkout_res["record"]}

                elif new_status == "已取消":
                    res["status"] = "已取消"
                    return {"success": True, "reservation": res}

        return {"success": False, "error": "找不到該筆預訂紀錄"}

    def add_feedback(self, customer_name, rating, comment):
        """新增顧客意見回饋"""
        fb = {
            "fb_id": f"FB-{len(self.feedbacks)+1:03d}",
            "customer_name": customer_name or "匿名顧客",
            "rating": int(rating),
            "comment": comment,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.feedbacks.append(fb)
        return {"success": True, "feedback": fb}

    def get_summary_for_c2_and_a1(self):
        """匯出銷售與預訂資料，供 C2 經營報表與 A1 需求預測呼叫使用"""
        total_revenue = sum(r["total_amount"] for r in self.sales_records)
        return {
            "total_revenue": total_revenue,
            "total_orders": len(self.sales_records),
            "sales_records": self.sales_records,
            "active_reservations": [r for r in self.reservations if r["status"] == "預訂中"],
            "feedbacks": self.feedbacks
        }