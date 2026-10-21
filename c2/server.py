"""C2 standalone API server on port 8002; no third-party packages."""
import argparse
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,unquote
from service import DB, connect, expenses, save_expense, delete_expense, adapt_b2_waste
ROOT=Path(__file__).resolve().parent
TEAM=ROOT.parent
B2_DB=Path(os.environ.get('B2_DB_PATH',str(TEAM/'b2_local.db')))

class Handler(BaseHTTPRequestHandler):
    def send_json(self,status,data):
        raw=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
    def send_file(self,path):
        if not path.is_file():return self.send_json(404,{'error':'檔案不存在'})
        raw=path.read_bytes();mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        if path.suffix=='.mjs':mime='text/javascript'
        self.send_response(200);self.send_header('Content-Type',mime+'; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        url=urlsplit(self.path);path=url.path
        if path=='/api/c2/health':return self.send_json(200,{'ok':True,'module':'c2','database':'sqlite'})
        if path=='/api/c2/expenses':return self.send_json(200,expenses())
        if path=='/api/c2/b2-waste':
            try:
                sys.path.insert(0,str(TEAM))
                from b2_inventory import InventoryService
                if not B2_DB.exists():return self.send_json(200,{'records':[],'warning':'B2 尚無資料庫，請先於 B2 系統建立報廢資料'})
                svc=InventoryService(str(B2_DB))
                try: data=svc.waste_report()
                finally:svc.close()
                return self.send_json(200,{'records':adapt_b2_waste(data),'source':'b2-sqlite'})
            except Exception as exc:return self.send_json(503,{'error':'B2 同步失敗：'+str(exc)})
        if path=='/api/c2/sales':
            with connect() as db:rows=db.execute('SELECT json FROM imported_sales ORDER BY id').fetchall()
            return self.send_json(200,[json.loads(r['json']) for r in rows])
        if path=='/' or path=='/c2':return self.send_file(ROOT/'index.html')
        # Path.resolve confines static files to the C2 directory
        candidate=(ROOT/unquote(path.lstrip('/'))).resolve()
        if not candidate.is_relative_to(ROOT) or not candidate.is_file() or candidate.suffix not in ('.css','.js','.mjs','.json','.html'):
            return self.send_json(404,{'error':'找不到路徑'})
        return self.send_file(candidate)
    def _body(self):
        origin=self.headers.get('Origin');host=self.headers.get('Host','')
        if origin and origin != 'http://'+host:raise PermissionError('禁止跨網站修改')
        n=int(self.headers.get('Content-Length','0'))
        if not 0<n<=2000000:raise ValueError('請求資料大小不正確')
        obj=json.loads(self.rfile.read(n))
        if not isinstance(obj,dict):raise ValueError('資料必須為物件')
        return obj
    def do_POST(self):
        try:
            body=self._body()
            if self.path=='/api/c2/expenses':return self.send_json(201,save_expense(body))
            if self.path=='/api/c2/sales':
                rows=body.get('sales')
                if not isinstance(rows,list):raise ValueError('sales 必須是陣列')
                # C1 adapter format intentionally matches existing analytics.mjs schema.
                # Validate in Node/browser before posting; server enforces minimal shape.
                from datetime import date
                clean=[]
                for row in rows:
                    if not isinstance(row,dict) or not row.get('id') or not isinstance(row.get('items'),list) or not row['items']:raise ValueError('銷售資料需要 id 與 items')
                    date.fromisoformat(str(row.get('date','')))
                    clean.append((str(row['id']),json.dumps(row,ensure_ascii=False)))
                with connect() as db:
                    for key,value in clean:db.execute('INSERT INTO imported_sales(id,json,updated_at) VALUES(?,?,datetime("now")) ON CONFLICT(id) DO UPDATE SET json=excluded.json,updated_at=excluded.updated_at',(key,value))
                return self.send_json(200,{'count':len(clean)})
            return self.send_json(404,{'error':'API 不存在'})
        except PermissionError as ex:return self.send_json(403,{'error':str(ex)})
        except (ValueError,TypeError,KeyError,json.JSONDecodeError) as ex:return self.send_json(400,{'error':str(ex)})
        except Exception:return self.send_json(500,{'error':'伺服器內部錯誤'})
    def do_PUT(self):
        if not self.path.startswith('/api/c2/expenses/'):return self.send_json(404,{'error':'API 不存在'})
        try:return self.send_json(200,save_expense(self._body(),record_id=unquote(self.path.rsplit('/',1)[1])))
        except LookupError as ex:return self.send_json(404,{'error':str(ex)})
        except (ValueError,PermissionError) as ex:return self.send_json(400,{'error':str(ex)})
    def do_DELETE(self):
        if not self.path.startswith('/api/c2/expenses/'):return self.send_json(404,{'error':'API 不存在'})
        try:
            origin=self.headers.get('Origin')
            if origin and origin!='http://'+self.headers.get('Host',''):return self.send_json(403,{'error':'禁止跨網站修改'})
            delete_expense(unquote(self.path.rsplit('/',1)[1]));return self.send_json(200,{'ok':True})
        except LookupError as ex:return self.send_json(404,{'error':str(ex)})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8002);a=parser.parse_args()
    print('C2 經營分析：http://127.0.0.1:'+str(a.port),flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
