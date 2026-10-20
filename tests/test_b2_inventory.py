import tempfile
import unittest
from datetime import date, timedelta
from b2_inventory import InventoryService, InventoryError

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.s=InventoryService(self.tmp.name+"/test.db")
        self.batch=self.s.receive_batch("ingredient","chicken","CH-001",
            (date.today()+timedelta(days=2)).isoformat(),10,"kg",120,"A2")
    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()
    def test_expiry(self):
        self.assertEqual(len(self.s.expiry_alerts(3)),1)
        self.assertEqual(len(self.s.expiry_alerts(1)),0)
    def test_stocktake(self):
        result=self.s.stocktake(self.batch,8,"B2")
        self.assertEqual(result["difference"],-2)
        self.assertEqual(self.s.batches()[0]["quantity"],8)
    def test_waste_and_loss(self):
        result=self.s.waste(self.batch,2,"expired","B2")
        self.assertEqual(result["loss"],240)
        self.assertEqual(self.s.waste_report()["total_loss"],240)
        self.assertEqual(self.s.batches()[0]["quantity"],8)
    def test_reject_overdraw(self):
        with self.assertRaises(InventoryError):
            self.s.waste(self.batch,11,"expired","B2")
        self.assertEqual(self.s.batches()[0]["quantity"],10)
    def test_reject_negative_count(self):
        with self.assertRaises(InventoryError):
            self.s.stocktake(self.batch,-1,"B2")

if __name__=="__main__": unittest.main()
