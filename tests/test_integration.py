"""跨組整合測試:用 demo.py 跑完整流程,檢查各組資料有沒有真的接上。"""
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import demo  # noqa: E402
from b2_inventory import InventoryService, InventoryError  # noqa: E402

NOW = datetime(2026, 11, 14, 11, 0)


class TestB2Consumption(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = InventoryService(str(Path(self.tmp.name) / "b2.db"))

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def test_fefo_consume_and_timed_expiry(self):
        self.s.receive_batch("bento", "chicken_bento", "LATE", "2026-11-14T17:00", 5, "份", 44, "B1")
        self.s.receive_batch("bento", "chicken_bento", "EARLY", "2026-11-14T13:00", 2, "份", 40, "B1")
        self.assertEqual(self.s.available("bento", "chicken_bento", NOW), 7)
        self.assertEqual(self.s.available("bento", "chicken_bento", NOW.replace(hour=14)), 5)  # 13:00 那批已過期
        used = self.s.consume("bento", "chicken_bento", 3, "sale", "C1", NOW)
        self.assertEqual([(u["batch_code"], u["quantity"]) for u in used], [("EARLY", 2), ("LATE", 1)])
        with self.assertRaises(InventoryError):
            self.s.consume("bento", "chicken_bento", 99, "sale", "C1", NOW)
        self.assertEqual(self.s.available("bento", "chicken_bento", NOW), 4)  # 失敗時不扣

    def test_date_only_expiry_usable_until_end_of_day(self):
        self.s.receive_batch("ingredient", "rice", "R1", "2026-11-14", 1000, "g", 0.04, "A2")
        self.assertEqual(self.s.available("ingredient", "rice", NOW.replace(hour=23)), 1000)
        self.assertEqual(self.s.available("ingredient", "rice", NOW + timedelta(days=1)), 0)

    def test_waste_report_date_filter(self):
        b = self.s.receive_batch("ingredient", "rice", "R1", "2026-12-01", 1000, "g", 0.04, "A2")
        self.s.waste(b, 100, "受潮", "B2")
        today = datetime.now().date().isoformat()
        self.assertEqual(len(self.s.waste_report(today, today)["records"]), 1)
        self.assertEqual(self.s.waste_report("2000-01-01", "2000-01-31")["records"], [])
        self.assertEqual(self.s.waste_report()["records"][0]["item_id"], "rice")


class TestWholeFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.db, cls.a2, cls.c2 = d / "bento.db", d / "a2.sqlite3", d / "c2.sqlite3"
        cls.r = demo.run(str(cls.db), cls.a2, cls.c2, now=datetime.now().replace(hour=12, minute=0), quiet=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def query(self, sql, args=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def test_one_shared_database_with_one_set_of_codes(self):
        bento_codes = {r[0] for r in self.query("SELECT code FROM Item WHERE type='bento'")}
        sold = {r[0] for r in self.query("SELECT DISTINCT product_code FROM c1_sale_items")}
        stocked = {r[0] for r in self.query("SELECT DISTINCT item_id FROM inventory_batches WHERE item_type='bento'")}
        self.assertTrue(sold <= bento_codes and stocked <= bento_codes)
        units = set(self.query("SELECT DISTINCT unit FROM inventory_batches WHERE item_type='ingredient'"))
        self.assertEqual(units, {("g",)})

    def test_a2_receipt_becomes_b2_stock(self):
        rows = self.query("SELECT batch_code FROM inventory_batches WHERE item_type='ingredient' AND batch_code LIKE 'A2-%'")
        self.assertEqual(len(rows), 4)

    def test_c1_sale_uses_b1_cost_and_deducts_b2(self):
        cost = self.query("SELECT unit_cost FROM ProductionBatch WHERE product_code='chicken_bento'")[0][0]
        sold = self.query("SELECT unit_cost FROM c1_sale_items i JOIN c1_sales s ON s.id=i.sale_id "
                          "WHERE s.source='pos' AND product_code='chicken_bento'")
        self.assertTrue(sold and all(abs(c[0] - cost) < 1e-6 for c in sold))
        left = self.query("SELECT quantity FROM inventory_batches WHERE item_type='bento' AND item_id='chicken_bento'")[0][0]
        self.assertEqual(left, 40 - 9 - 2)  # 生產 40,賣 9,報廢 2

    def test_c2_receives_all_c1_sales(self):
        conn = sqlite3.connect(self.c2)
        try:
            count = conn.execute("SELECT COUNT(*) FROM imported_sales").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, self.query("SELECT COUNT(*) FROM c1_sales")[0][0])
        self.assertEqual(self.r["synced"], count)
        self.assertGreater(self.r["waste"]["total_loss"], 0)

    def test_a1_forecasts_from_live_data(self):
        data = self.r["a1_input"]["data"]
        self.assertTrue(data["sales"] and data["stock"] and data["incoming"])
        forecasts = {(f["product_id"], f["slot"]): f for f in self.r["a1"]["forecasts"]}
        self.assertGreater(forecasts[("chicken_bento", "午餐")]["walk_in"], 0)
        self.assertEqual(forecasts[("chicken_bento", "午餐")]["preorders"], 10)
        self.assertEqual(forecasts[("giant_3kg", "午餐")]["total"], 2)
        self.assertEqual(forecasts[("giant_3kg", "午餐")]["method"], "接單製作（只計預訂）")
        self.assertFalse([w for w in self.r["a1"]["warnings"] if "巨無霸" in w or "大胃王" in w])
        incoming = {p["ingredient_id"]: p["incoming"] for p in self.r["a1"]["purchases"]}
        self.assertGreater(incoming["chicken"], 0)  # A2 明天到貨的雞腿肉算進在途


if __name__ == "__main__":
    unittest.main()
