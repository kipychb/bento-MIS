"""Local A1 API and SQLite calculation snapshots; Python standard library only."""
import argparse
from datetime import date, timedelta, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from core import calculate

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'plans.sqlite3'


def sample():
    today = date.today()
    target = today + timedelta(days=3)
    products = [dict(id='chicken', name='雞腿便當', recipe={'rice':100, 'chicken':200, 'vegetable':150}), dict(id='tofu', name='豆腐便當', recipe={'rice':100, 'tofu':180, 'vegetable':150})]
    ingredients = [dict(id=i, name=n, unit='g', pack_size=p, lead_days=2, capacity=200000) for i,n,p in [('rice','生米',5000),('chicken','雞腿肉',1000),('vegetable','蔬菜',1000),('tofu','豆腐',600)]]
    sales=[]
    for days in range(28,0,-1):
        d=today-timedelta(days=days)
        for p,base in [('chicken',35),('tofu',20)]:
            for slot,ratio in [('午餐',1),('晚餐',0.8),('宵夜',0.3)]:
                sales.append(dict(date=d.isoformat(),product_id=p,slot=slot,walk_in_qty=int(base*ratio)+(d.weekday()%3)))
    return dict(target=target.isoformat(), factor=1, buffer=0.1, data=dict(products=products, ingredients=ingredients, slots=['午餐','晚餐','宵夜'], sales=sales,
        preorders=[dict(date=target.isoformat(),product_id='chicken',slot='午餐',qty=10,status='confirmed')],
        stock=[dict(ingredient_id=i['id'],qty=5000,reserved_qty=500,expires_on=(target+timedelta(days=3)).isoformat()) for i in ingredients],
        incoming=[dict(ingredient_id='rice',qty=5000,arrival_date=target.isoformat(),expires_on=(target+timedelta(days=30)).isoformat(),status='confirmed')]))


def connect():
    DB.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(DB)
    conn.execute('CREATE TABLE IF NOT EXISTS plans (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, target TEXT NOT NULL, input_json TEXT NOT NULL, result_json TEXT NOT NULL)')
    return conn


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, mime='application/json; charset=utf-8'):
        raw=json.dumps(body, ensure_ascii=False).encode() if mime.startswith('application/json') else body
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path=='/api/a1/sample':
            return self.send(200,sample())
        if self.path=='/api/a1/plans':
            with connect() as conn:
                rows=conn.execute('SELECT id,created_at,target FROM plans ORDER BY id DESC LIMIT 30').fetchall()
            return self.send(200,[dict(id=r[0],created_at=r[1],target=r[2]) for r in rows])
        if self.path.startswith('/api/a1/plans/'):
            try:
                plan_id=int(self.path.rsplit('/',1)[1])
            except ValueError:
                return self.send(400,{'error':'紀錄 id 格式錯誤'})
            with connect() as conn:
                row=conn.execute('SELECT input_json,result_json FROM plans WHERE id=?',(plan_id,)).fetchone()
            return self.send(200,dict(input=json.loads(row[0]),result=json.loads(row[1]))) if row else self.send(404,{'error':'紀錄不存在'})
        files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
        if self.path not in files:
            return self.send(404,{'error':'找不到頁面'})
        name,mime=files[self.path]
        self.send(200,(ROOT/'web'/name).read_bytes(),mime)

    def do_POST(self):
        if self.path not in ('/api/a1/calculate','/api/a1/plans'):
            return self.send(404,{'error':'找不到 API'})
        # Block browser requests originating on another website.
        origin=self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host',''):
            return self.send(403,{'error':'不允許跨網站請求'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=2000000:
                raise ValueError('請求大小必須介於 1 byte 與 2 MB')
            body=json.loads(self.rfile.read(length))
            result=calculate(body['data'],body['target'],body.get('factor',1),body.get('buffer',0.1))
        except (ValueError, KeyError, TypeError, OverflowError, AttributeError) as error:
            return self.send(400,{'error':str(error)})
        if self.path.endswith('/calculate'):
            return self.send(200,result)
        with connect() as conn:
            cursor=conn.execute('INSERT INTO plans(created_at,target,input_json,result_json) VALUES(?,?,?,?)',(datetime.now(timezone.utc).isoformat(),result['target'],json.dumps(body,ensure_ascii=False),json.dumps(result,ensure_ascii=False)))
            plan_id=cursor.lastrowid
        self.send(201,{'id':plan_id,'result':result})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    print(f'A1 預測與採購工作台：http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
