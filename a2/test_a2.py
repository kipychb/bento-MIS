import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

sys.path.append(str(Path(__file__).resolve().parent.parent))
from b2_inventory import InventoryService
from service import PurchasingService, PurchaseError, CHECKS, seed
import server

OK = {k: True for k in CHECKS}
D = '2026-10-28'


class Base(unittest.TestCase):
    def setUp(self):
        self.b2 = InventoryService(':memory:')
        self.s = PurchasingService(':memory:', self.b2)
        self.s.upsert_ingredient('chicken', '雞腿肉', 'g', 3, 30000)
        self.sup = self.s.add_supplier('肉商', 1, 0)
        for min_packs, price in [(1, 165), (20, 150)]:
            self.s.set_price(self.sup, 'chicken', 1000, min_packs, price)
    def tearDown(self):
        self.s.close(); self.b2.close()
    def ordered(self, packs=10, deliveries=None):
        po = self.s.create_po(self.sup, [dict(ingredient_id='chicken', packs=packs, pack_size=1000, deliveries=deliveries)], 'A2', D)
        return self.s.confirm_po(po['id'])


class Compare(Base):
    def test_tier_price_and_next_tier_hint(self):
        o = self.s.compare('chicken', 15000, '2026-10-30', D)['offers'][0]
        self.assertEqual((o['packs'], o['price_per_pack'], o['total']), (15, 165, 2475))
        self.assertEqual((o['next_tier']['packs'], o['next_tier']['total'], o['next_tier']['extra_qty']), (20, 3000, 5000))
        self.assertEqual(self.s.compare('chicken', 20000, None, D)['offers'][0]['total'], 3000)
    def test_pack_rounding_and_minimum(self):
        other = self.s.add_supplier('大批發', 1, 0)
        self.s.set_price(other, 'chicken', 2000, 10, 280)
        offers = {o['supplier_name']: o for o in self.s.compare('chicken', 1500, None, D)['offers']}
        self.assertEqual(offers['肉商']['packs'], 2)
        self.assertEqual(offers['大批發']['packs'], 10)
        self.assertTrue(offers['大批發']['warnings'])
    def test_late_supplier_ranked_after_on_time(self):
        slow = self.s.add_supplier('便宜但慢', 5, 0)
        self.s.set_price(slow, 'chicken', 1000, 1, 100)
        offers = self.s.compare('chicken', 5000, '2026-10-30', D)['offers']
        self.assertEqual([o['supplier_name'] for o in offers], ['肉商', '便宜但慢'])
        self.assertFalse(offers[1]['on_time'])
    def test_inactive_supplier_excluded(self):
        self.s.set_supplier_active(self.sup, False)
        self.assertEqual(self.s.compare('chicken', 1000)['offers'], [])
    def test_from_a1_result(self):
        a1 = dict(target='2026-10-30', purchases=[dict(ingredient_id='chicken', purchase=3000, warnings=['超過容量']),
                                                  dict(ingredient_id='rice', purchase=0), dict(ingredient_id='tofu', purchase=600)])
        r = self.s.suggestions_from_a1(a1, D)
        self.assertEqual([i['ingredient_id'] for i in r['items']], ['chicken'])
        self.assertEqual(r['items'][0]['a1_warnings'], ['超過容量'])
        self.assertEqual(len(r['warnings']), 1)


class Orders(Base):
    def test_po_number_price_and_default_delivery(self):
        po = self.s.create_po(self.sup, [dict(ingredient_id='chicken', packs=20, pack_size=1000)], 'A2', D)
        self.assertEqual((po['po_no'], po['status'], po['amount']), ('PO20261028-001', 'draft', 3000))
        self.assertEqual([(d['scheduled_date'], d['packs']) for d in po['lines'][0]['deliveries']], [('2026-10-29', 20)])
    def test_split_plan(self):
        self.assertEqual(self.s.split_plan(10, '2026-10-29', 4, 2),
                         [dict(date='2026-10-29', packs=4), dict(date='2026-10-31', packs=3), dict(date='2026-11-02', packs=3)])
    def test_split_must_sum_and_respect_capacity(self):
        with self.assertRaises(PurchaseError):
            self.s.create_po(self.sup, [dict(ingredient_id='chicken', packs=10, pack_size=1000, deliveries=[dict(date='2026-10-29', packs=9)])], 'A2', D)
        with self.assertRaises(PurchaseError):  # 40 kg in one day > 30 kg capacity
            self.s.create_po(self.sup, [dict(ingredient_id='chicken', packs=40, pack_size=1000)], 'A2', D)
        po = self.s.create_po(self.sup, [dict(ingredient_id='chicken', packs=40, pack_size=1000,
                                              deliveries=self.s.split_plan(40, '2026-10-29', 20))], 'A2', D)
        self.assertEqual(len(po['lines'][0]['deliveries']), 2)
    def test_minimum_order_amount_blocks_confirm(self):
        self.s.db.execute('UPDATE suppliers SET min_order_amount=5000')
        po = self.s.create_po(self.sup, [dict(ingredient_id='chicken', packs=10, pack_size=1000)], 'A2', D)
        with self.assertRaises(PurchaseError):
            self.s.confirm_po(po['id'])
    def test_incoming_only_after_confirm(self):
        po = self.s.create_po(self.sup, [dict(ingredient_id='chicken', packs=10, pack_size=1000)], 'A2', D)
        self.assertEqual(self.s.incoming(), [])
        self.s.confirm_po(po['id'])
        row = self.s.incoming()[0]
        self.assertEqual((row['qty'], row['arrival_date'], row['expires_on'], row['status']), (10000, '2026-10-29', '2026-11-01', 'confirmed'))
    def test_reschedule_updates_incoming(self):
        po = self.ordered()
        self.s.reschedule(po['lines'][0]['deliveries'][0]['id'], '2026-10-30', '延後')
        self.assertEqual(self.s.incoming()[0]['arrival_date'], '2026-10-30')
    def test_cancel(self):
        po = self.ordered()
        self.assertEqual(self.s.cancel_po(po['id'], '菜單調整')['status'], 'cancelled')
        self.assertEqual(self.s.incoming(), [])
        with self.assertRaises(PurchaseError):
            self.s.cancel_po(po['id'], '再取消')
    def test_a1_ingredients_uses_cheapest_offer(self):
        self.assertEqual(self.s.a1_ingredients(), [dict(id='chicken', name='雞腿肉', unit='g', pack_size=1000, lead_days=1, capacity=30000)])


class Receiving(Base):
    def test_accept_writes_b2_batch_with_unit_cost(self):
        po = self.ordered(10)
        d = po['lines'][0]['deliveries'][0]
        r = self.s.receive(d['id'], 10, 10, '2026-11-01', '驗收員', OK, 'LOT-9', received_date='2026-10-29')
        batch = self.b2.batches()[0]
        self.assertEqual((batch['item_id'], batch['quantity'], batch['unit'], batch['unit_cost'], batch['expiry_date']),
                         ('chicken', 10000, 'g', 0.165, '2026-11-01'))
        self.assertEqual((r['po']['status'], r['amount'], r['b2_batch_id']), ('received', 1650, batch['id']))
        self.assertEqual(self.s.incoming(), [])
    def test_split_deliveries_partial_then_received(self):
        po = self.ordered(10, self.s.split_plan(10, '2026-10-29', 5))
        first, second = po['lines'][0]['deliveries']
        self.assertEqual(self.s.receive(first['id'], 5, 5, '2026-11-01', 'A2', OK, received_date='2026-10-29')['po']['status'], 'partial')
        self.assertEqual(len(self.s.incoming()), 1)
        self.assertEqual(self.s.receive(second['id'], 5, 5, '2026-11-02', 'A2', OK, received_date='2026-10-30')['po']['status'], 'received')
        self.assertEqual(len(self.b2.batches()), 2)
    def test_reject_requires_reason_and_backorder(self):
        po = self.ordered(10)
        d = po['lines'][0]['deliveries'][0]
        with self.assertRaises(PurchaseError):
            self.s.receive(d['id'], 10, 8, '2026-11-01', 'A2', OK, received_date='2026-10-29')
        r = self.s.receive(d['id'], 10, 8, '2026-11-01', 'A2', OK, reject_reason='破袋', received_date='2026-10-29', backorder_date='2026-10-30')
        self.assertEqual((r['rejected_packs'], r['shortfall'], r['po']['status']), (2, 2, 'partial'))
        self.assertEqual(self.s.incoming()[0]['qty'], 2000)
    def test_failed_check_or_expired_cannot_accept(self):
        d = self.ordered(10)['lines'][0]['deliveries'][0]
        with self.assertRaises(PurchaseError):
            self.s.receive(d['id'], 10, 10, '2026-11-01', 'A2', dict(OK, temperature=False), received_date='2026-10-29')
        with self.assertRaises(PurchaseError):
            self.s.receive(d['id'], 10, 10, '2026-10-29', 'A2', OK, received_date='2026-10-29')
        self.assertEqual(self.b2.batches(), [])
        r = self.s.receive(d['id'], 10, 0, None, 'A2', dict(OK, temperature=False), reject_reason='溫度 12°C', received_date='2026-10-29')
        self.assertEqual((r['accepted_packs'], r['b2_batch_id'], r['po']['status']), (0, None, 'received'))
    def test_over_delivery_and_double_receive_blocked(self):
        d = self.ordered(10)['lines'][0]['deliveries'][0]
        with self.assertRaises(PurchaseError):
            self.s.receive(d['id'], 12, 12, '2026-11-01', 'A2', OK, received_date='2026-10-29')
        self.s.receive(d['id'], 12, 10, '2026-11-01', 'A2', OK, reject_reason='超量退回', received_date='2026-10-29')
        with self.assertRaises(PurchaseError):
            self.s.receive(d['id'], 1, 1, '2026-11-01', 'A2', OK, received_date='2026-10-29')
    def test_draft_cannot_be_received(self):
        po = self.s.create_po(self.sup, [dict(ingredient_id='chicken', packs=10, pack_size=1000)], 'A2', D)
        with self.assertRaises(PurchaseError):
            self.s.receive(po['lines'][0]['deliveries'][0]['id'], 10, 10, '2026-11-01', 'A2', OK)
    def test_spend_report_and_scorecard(self):
        po = self.ordered(10, self.s.split_plan(10, '2026-10-29', 5))
        first, second = po['lines'][0]['deliveries']
        self.s.receive(first['id'], 5, 5, '2026-11-01', 'A2', OK, price_per_pack=170, received_date='2026-10-29')
        self.s.receive(second['id'], 5, 4, '2026-11-02', 'A2', OK, reject_reason='破袋', received_date='2026-10-31')
        r = self.s.spend_report()
        s = r['suppliers'][0]
        self.assertEqual(r['total_amount'], 5 * 170 + 4 * 165)
        self.assertEqual((s['on_time_rate'], s['acceptance_rate'], s['price_variance']), (0.5, 0.9, 25))
        self.assertEqual(self.s.spend_report('2026-10-30')['total_amount'], 660)
    def test_partial_close_cancels_remaining(self):
        po = self.ordered(10, self.s.split_plan(10, '2026-10-29', 5))
        self.s.receive(po['lines'][0]['deliveries'][0]['id'], 5, 5, '2026-11-01', 'A2', OK, received_date='2026-10-29')
        self.assertEqual(self.s.cancel_po(po['id'], '需求下降')['status'], 'closed')
        self.assertEqual(self.s.incoming(), [])


class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        server.DB = Path(cls.temp.name) / 'a2.sqlite3'
        server.B2_DB = str(Path(cls.temp.name) / 'b2.db')
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True); cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join(); cls.temp.cleanup()
    def request(self, path, body=None):
        req = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json'})
        with urlopen(req) as response:
            return json.load(response)['result']
    def test_full_flow_reaches_b2(self):
        self.request('/api/a2/seed', {})
        sup = next(s for s in self.request('/api/a2/suppliers') if 'rice' in {p['ingredient_id'] for p in s['prices']})
        po = self.request('/api/a2/pos', dict(supplier_id=sup['id'], created_by='A2', lines=[dict(ingredient_id='rice', packs=10, pack_size=5000)]))
        self.request(f"/api/a2/pos/{po['id']}/confirm", {})
        d = self.request('/api/a2/schedule')[0]
        self.assertEqual(self.request('/api/a2/incoming')[0]['qty'], 50000)
        r = self.request(f"/api/a2/deliveries/{d['delivery_id']}/receive", dict(received_packs=10, accepted_packs=10, expiry_date='2099-01-01', inspector='A2', checks=OK))
        self.assertEqual(r['po']['status'], 'received')
        b2 = InventoryService(server.B2_DB)
        try:
            self.assertEqual(b2.batches()[0]['quantity'], 50000)
        finally:
            b2.close()
        self.assertEqual(self.request('/api/a2/spend')['total_amount'], 2100)
        with urlopen(self.base + '/') as response:
            self.assertEqual(response.status, 200)
    def test_errors_are_400_or_404(self):
        with self.assertRaises(HTTPError) as e:
            self.request('/api/a2/suppliers', dict(name='', lead_days=1))
        self.assertEqual(e.exception.code, 400)
        with self.assertRaises(HTTPError) as e:
            self.request('/api/a2/nope')
        self.assertEqual(e.exception.code, 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
