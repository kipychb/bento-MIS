import unittest
from service import C1POSService

class TestC1POSService(unittest.TestCase):
    def setUp(self):
        self.service = C1POSService()

    def test_pos_checkout_and_stock_deduction(self):
        """測試 POS 結帳是否正確計算金額並扣除成品庫存"""
        initial_stock = self.service.menu["B01"]["stock"]
        res = self.service.pos_checkout([{"id": "B01", "qty": 3}], payment_method="現金")
        self.assertTrue(res["success"])
        self.assertEqual(res["record"]["total_amount"], 150)  # 50元 * 3份
        self.assertEqual(self.service.menu["B01"]["stock"], initial_stock - 3)

    def test_pos_checkout_insufficient_stock(self):
        """測試成品庫存不足時的防呆攔截機制"""
        res = self.service.pos_checkout([{"id": "B04", "qty": 99}], payment_method="現金")
        self.assertFalse(res["success"])
        self.assertIn("庫存不足", res["error"])

    def test_reservation_pickup_and_cancel(self):
        """測試顧客預訂、取餐轉營收扣庫存，以及取消預訂流程"""
        # 1. 建立預訂
        create_res = self.service.create_reservation(
            customer_name="Pooh",
            phone="0912345678",
            pickup_time="12:30",
            items=[{"id": "B03", "qty": 2}]
        )
        self.assertTrue(create_res["success"])
        res_id = create_res["reservation"]["res_id"]

        # 2. 執行取餐（應自動轉為營收並扣除 B03 庫存）
        stock_before = self.service.menu["B03"]["stock"]
        pickup_res = self.service.update_reservation_status(res_id, "已取餐")
        self.assertTrue(pickup_res["success"])
        self.assertEqual(self.service.menu["B03"]["stock"], stock_before - 2)
        self.assertEqual(self.service.get_summary_for_c2_and_a1()["total_revenue"], 300)

        # 3. 建立另一筆預訂並測試取消
        create_res2 = self.service.create_reservation("王小明", "0987654321", "18:00", [{"id": "B01", "qty": 1}])
        res_id2 = create_res2["reservation"]["res_id"]
        cancel_res = self.service.update_reservation_status(res_id2, "已取消")
        self.assertTrue(cancel_res["success"])
        self.assertEqual(cancel_res["reservation"]["status"], "已取消")

if __name__ == "__main__":
    unittest.main()