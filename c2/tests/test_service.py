import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from service import save_expense,expenses,delete_expense,adapt_b2_waste
class TestC2(unittest.TestCase):
 def test_crud(self):
  with tempfile.TemporaryDirectory() as d:
   db=Path(d)/'c2.db'
   row=save_expense({'date':'2026-10-21','category':'rent','amount':12500,'description':'房租'},db)
   self.assertEqual(expenses(db)[0]['amount'],12500)
   save_expense({'date':'2026-10-21','category':'rent','amount':12000},db,row['id'])
   self.assertEqual(expenses(db)[0]['amount'],12000)
   delete_expense(row['id'],db);self.assertEqual(expenses(db),[])
 def test_bad_amount(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):save_expense({'date':'2026-10-21','category':'rent','amount':-1},Path(d)/'db')
 def test_waste(self):
  source={'total_loss':999,'records':[{'batch_id':1,'loss':120,'created_at':'2026-10-21 14:00:00','reason':'過期'}]}
  rows=adapt_b2_waste(source);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['amount'],120)
if __name__=='__main__':unittest.main()
