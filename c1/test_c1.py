import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service import C1POSService  # noqa: E402

NOW = datetime(2026, 11, 14, 12, 30)


class TestC1POSService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "bento.db")
        self.service = C1POSService(self.path)
        inv = self.service.inventory
        # 兩批雞腿便當:較早到期的一批先賣(FEFO)
        inv.receive_batch("bento", "chicken_bento", "CB-1", (NOW + timedelta(hours=2)).isoformat(), 3, "份", 40, "B1")
        inv.receive_batch("bento", "chicken_bento", "CB-2", (NOW + timedelta(hours=5)).isoformat(), 10, "份", 44, "B1")
        inv.receive_batch("bento", "giant_1kg", "G1-1", (NOW + timedelta(hours=5)).isoformat(), 2, "份", 90, "B1")

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def stock(self, code):
        return {m["id"]: m["stock"] for m in self.service.get_menu(NOW)}[code]

    def test_menu_comes_from_master_and_b2(self):
        menu = {m["id"]: m for m in self.service.get_menu(NOW)}
        self.assertEqual(menu["chicken_bento"]["price"], 75)
        self.assertEqual(menu["chicken_bento"]["stock"], 13)
        self.assertEqual(menu["tofu_bento"]["stock"], 0)
        self.assertTrue(menu["giant_1kg"]["is_giant"])

    def test_pos_checkout_deducts_b2_stock_fefo_and_records_cost(self):
        res = self.service.pos_checkout([{"id": "chicken_bento", "qty": 5}], "現金", now=NOW)
        self.assertTrue(res["success"], res)
        self.assertEqual(res["record"]["total_amount"], 375)
        self.assertEqual(self.stock("chicken_bento"), 8)
        # 3 份 × 40 + 2 份 × 44 = 208 → 每份 41.6
        self.assertAlmostEqual(res["record"]["items"][0]["unit_cost"], 41.6)
        batches = {b["batch_code"]: b["quantity"] for b in self.service.inventory.batches("bento")}
        self.assertEqual(batches["CB-1"], 0)
        self.assertEqual(batches["CB-2"], 8)

    def test_insufficient_stock_blocks_and_changes_nothing(self):
        res = self.service.pos_checkout([{"id": "chicken_bento", "qty": 2}, {"id": "giant_1kg", "qty": 9}], now=NOW)
        self.assertFalse(res["success"])
        self.assertIn("庫存不足", res["error"])
        self.assertEqual(self.stock("chicken_bento"), 13)
        self.assertEqual(self.service.sales_for_c2(), [])

    def test_expired_bento_is_not_sold(self):
        later = NOW + timedelta(hours=3)  # CB-1 已過期
        self.assertEqual(self.stock("chicken_bento"), 13)
        self.assertEqual({m["id"]: m["stock"] for m in self.service.get_menu(later)}["chicken_bento"], 10)

    def test_data_survives_restart(self):
        self.service.pos_checkout([{"id": "chicken_bento", "qty": 1}], now=NOW)
        self.service.add_feedback("小明", 5, "份量很大", now=NOW)
        self.service.close()
        self.service = C1POSService(self.path)
        self.assertEqual(len(self.service.sales_for_c2()), 1)
        self.assertEqual(self.service.feedbacks[0]["comment"], "份量很大")

    def test_reservation_pickup_and_cancel(self):
        created = self.service.create_reservation("Pooh", "0912345678", "2026-11-14T18:30",
                                                  [{"id": "giant_1kg", "qty": 2}], now=NOW)
        self.assertTrue(created["success"], created)
        res_id = created["reservation"]["res_id"]
        self.assertEqual(created["reservation"]["slot"], "晚餐")
        self.assertEqual(self.service.preorders_for_a1(),
                         [{"date": "2026-11-14", "product_id": "giant_1kg", "slot": "晚餐", "qty": 2, "status": "confirmed"}])

        pickup = self.service.update_reservation_status(res_id, "已取餐", now=NOW)
        self.assertTrue(pickup["success"], pickup)
        self.assertEqual(self.stock("giant_1kg"), 0)
        self.assertEqual(pickup["reservation"]["status"], "已取餐")
        self.assertEqual(self.service.sales_for_c2()[0]["source"], "preorder")
        self.assertEqual(self.service.walk_in_for_a1(), [])  # 預訂取餐不算現場銷量

        second = self.service.create_reservation("王小明", "0987654321", "19:00", [{"id": "chicken_bento", "qty": 1}], now=NOW)
        cancel = self.service.update_reservation_status(second["reservation"]["res_id"], "已取消", now=NOW)
        self.assertEqual(cancel["reservation"]["status"], "已取消")
        again = self.service.update_reservation_status(second["reservation"]["res_id"], "已取餐", now=NOW)
        self.assertFalse(again["success"])

    def test_pickup_without_stock_keeps_reservation_open(self):
        created = self.service.create_reservation("大胃王", "0911", "2026-11-14T19:00", [{"id": "giant_3kg", "qty": 1}], now=NOW)
        res = self.service.update_reservation_status(created["reservation"]["res_id"], "已取餐", now=NOW)
        self.assertFalse(res["success"])
        self.assertEqual(self.service.reservations[0]["status"], "預訂中")

    def test_exports_for_a1_and_c2(self):
        self.service.pos_checkout([{"id": "chicken_bento", "qty": 2}], now=NOW)
        self.service.pos_checkout([{"id": "chicken_bento", "qty": 1}], now=NOW.replace(hour=17, minute=10))
        self.service.import_history([{"date": "2026-11-13", "local_time": "12:00", "product_code": "chicken_bento", "qty": 30, "unit_cost": 41}])
        self.assertEqual(self.service.walk_in_for_a1(), [
            {"date": "2026-11-13", "product_id": "chicken_bento", "slot": "午餐", "walk_in_qty": 30},
            {"date": "2026-11-14", "product_id": "chicken_bento", "slot": "午餐", "walk_in_qty": 2},
            {"date": "2026-11-14", "product_id": "chicken_bento", "slot": "晚餐", "walk_in_qty": 1},
        ])
        c2 = self.service.sales_for_c2("2026-11-14", "2026-11-14")
        self.assertEqual(len(c2), 2)
        self.assertEqual(set(c2[0]["items"][0]), {"productId", "productName", "quantity", "unitPrice", "unitCost"})

    def test_invalid_input(self):
        self.assertFalse(self.service.pos_checkout([{"id": "nope", "qty": 1}], now=NOW)["success"])
        self.assertFalse(self.service.pos_checkout([{"id": "chicken_bento", "qty": 0}], now=NOW)["success"])
        self.assertFalse(self.service.create_reservation("A", "1", "2026-11-13T12:00", [{"id": "chicken_bento", "qty": 1}], now=NOW)["success"])
        self.assertFalse(self.service.add_feedback("A", 9, "x", now=NOW)["success"])


class TestC1API(unittest.TestCase):
    def test_http_flow(self):
        import server
        with tempfile.TemporaryDirectory() as tmp:
            server.app.config["DB_PATH"] = os.path.join(tmp, "bento.db")
            with server.service() as s:
                s.inventory.receive_batch("bento", "tofu_bento", "TB-1", (datetime.now() + timedelta(hours=6)).isoformat(), 4, "份", 35, "B1")
            client = server.app.test_client()
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.post("/api/c1/checkout", json={"items": [{"id": "tofu_bento", "qty": 1}]}).status_code, 200)
            self.assertEqual(client.post("/api/c1/checkout", json={"items": [{"id": "tofu_bento", "qty": 99}]}).status_code, 400)
            menu = {m["id"]: m["stock"] for m in client.get("/api/c1/menu").get_json()}
            self.assertEqual(menu["tofu_bento"], 3)
            self.assertEqual(len(client.get("/api/c1/sales").get_json()), 1)
            self.assertEqual(client.get("/api/c1/a1").get_json()["sales"][0]["product_id"], "tofu_bento")
            self.assertEqual(client.post("/api/c1/feedbacks", json={"rating": 4, "comment": "好吃"}).status_code, 200)
            self.assertEqual(client.get("/api/c1/export").get_json()["total_orders"], 1)


if __name__ == "__main__":
    unittest.main()
