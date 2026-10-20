import copy
from datetime import date,timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from core import calculate
import server


def fixture():
    return dict(products=[dict(id='p',name='便當',recipe={'rice':100})],ingredients=[dict(id='rice',name='生米',unit='g',pack_size=1000,lead_days=2,capacity=100000)],slots=['午餐'],sales=[dict(date='2026-10-12',product_id='p',slot='午餐',walk_in_qty=10),dict(date='2026-10-19',product_id='p',slot='午餐',walk_in_qty=20)],preorders=[dict(date='2026-10-26',product_id='p',slot='午餐',qty=5,status='confirmed')],stock=[dict(ingredient_id='rice',qty=500,reserved_qty=100,expires_on='2026-10-27')],incoming=[])


class Calculations(unittest.TestCase):
    def run_plan(self,data=None,**kwargs):
        return calculate(data or fixture(),'2026-10-26',as_of='2026-10-20',**kwargs)
    def test_weighted_forecast(self):
        f=self.run_plan()['forecasts'][0]
        self.assertEqual((f['baseline'],f['walk_in'],f['preorders'],f['total']),(16.67,17,5,22))
    def test_factor_not_applied_to_preorders(self):
        self.assertEqual(self.run_plan(factor=2)['forecasts'][0]['total'],39)
    def test_purchase_buffer_reserved_and_pack(self):
        p=self.run_plan()['purchases'][0]
        self.assertEqual((p['demand'],p['buffer'],p['available'],p['shortage'],p['purchase']),(2200,220,400,2020,3000))
    def test_expiry_cutoff(self):
        d=fixture();d['stock'][0]['expires_on']='2026-10-26'
        p=self.run_plan(d)['purchases'][0]
        self.assertEqual((p['available'],p['excluded']),(0,400))
    def test_incoming_filters(self):
        d=fixture()
        for status,arrival,expiry in [('confirmed','2026-10-25','2026-10-28'),('received','2026-10-25','2026-10-28'),('confirmed','2026-10-27','2026-10-28'),('confirmed','2026-10-25','2026-10-26')]:
            d['incoming'].append(dict(ingredient_id='rice',qty=500,arrival_date=arrival,expires_on=expiry,status=status))
        self.assertEqual(self.run_plan(d)['purchases'][0]['incoming'],500)
    def test_cancelled_preorders(self):
        d=fixture();d['preorders'][0]['status']='cancelled'
        self.assertEqual(self.run_plan(d)['forecasts'][0]['total'],17)
    def test_future_data_not_leaked(self):
        d=fixture();d['sales'].append(dict(date='2026-10-26',product_id='p',slot='午餐',walk_in_qty=999))
        self.assertEqual(self.run_plan(d)['forecasts'][0]['walk_in'],17)
    def test_no_history(self):
        d=fixture();d['sales']=[]
        r=self.run_plan(d);self.assertEqual(r['forecasts'][0]['total'],5);self.assertTrue(r['warnings'])
    def test_zero_sales_are_observations(self):
        d=fixture()
        for s in d['sales']:s['walk_in_qty']=0
        self.assertEqual(self.run_plan(d)['forecasts'][0]['walk_in'],0)
    def test_no_negative_purchase(self):
        d=fixture();d['stock'][0]['qty']=10000
        self.assertEqual(self.run_plan(d)['purchases'][0]['purchase'],0)
    def test_capacity_and_lead_warning(self):
        d=fixture();d['ingredients'][0].update(capacity=1000,lead_days=10)
        self.assertEqual(len(self.run_plan(d)['purchases'][0]['warnings']),2)
    def test_fallback(self):
        d=fixture();d['sales'][0]['date']='2026-10-13';d['sales'][1]['date']='2026-10-14'
        self.assertEqual(self.run_plan(d)['forecasts'][0]['method'],'最近七筆加權平均')
    def test_invalid_values(self):
        for value in [-1,True,float('nan'),'2']:
            d=fixture();d['stock'][0]['qty']=value
            with self.assertRaises(ValueError):self.run_plan(d)
        d=fixture();d['sales'].append(copy.deepcopy(d['sales'][0]))
        with self.assertRaises(ValueError):self.run_plan(d)
        with self.assertRaises(ValueError):self.run_plan(factor=4)
    def test_shared_ingredient_aggregated(self):
        d=fixture();d['products'].append(dict(id='p2',name='第二便當',recipe={'rice':50}))
        d['preorders'].append(dict(date='2026-10-26',product_id='p2',slot='午餐',qty=10,status='confirmed'))
        self.assertEqual(self.run_plan(d)['purchases'][0]['demand'],2700)


class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();server.DB=Path(cls.temp.name)/'plans.sqlite3'
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.http.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,path,body=None):
        req=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        with urlopen(req) as response:return json.load(response)
    def test_calculate_save_reload(self):
        data=self.request('/api/a1/sample')
        result=self.request('/api/a1/calculate',data)
        self.assertEqual(len(result['forecasts']),6)
        saved=self.request('/api/a1/plans',data)
        loaded=self.request('/api/a1/plans/'+str(saved['id']))
        self.assertEqual(loaded['result'],result)
        self.assertEqual(loaded['input'],data)
        self.assertTrue(self.request('/api/a1/plans'))
    def test_invalid_request_returns_400(self):
        with self.assertRaises(HTTPError) as e:self.request('/api/a1/calculate',{})
        self.assertEqual(e.exception.code,400)
    def test_static_routes(self):
        for path in ['/','/app.js','/style.css']:
            with urlopen(self.base+path) as response:self.assertEqual(response.status,200)
        with self.assertRaises(HTTPError):self.request('/../server.py')


if __name__=='__main__':unittest.main(verbosity=2)
