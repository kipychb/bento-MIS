"""C2 expense management (SQLite), B2 waste adapter and import snapshots. Standard library only."""
from __future__ import annotations
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'c2.sqlite3'
CATEGORIES = {'personnel', 'utilities', 'rent', 'other'}

def valid_date(value):
    if not isinstance(value,str): raise ValueError('日期格式錯誤')
    try: return date.fromisoformat(value).isoformat() if len(value)==10 else (_ for _ in ()).throw(ValueError())
    except ValueError: raise ValueError('日期必須為 YYYY-MM-DD')

def amount(value):
    try:
        n=Decimal(str(value))
        if not n.is_finite() or n<0 or n.as_tuple().exponent < -2: raise ValueError()
        return int(n*100)
    except (InvalidOperation,ValueError,TypeError): raise ValueError('金額必須為非負、最多兩位小數')

@contextmanager
def connect(path=DB):
    """開啟、交易、關閉一次完成；Windows 上沒關閉的連線會讓資料庫檔案被鎖住。"""
    conn=_open(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def _open(path=DB):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(str(path),timeout=10)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA busy_timeout=10000')
    conn.execute('''CREATE TABLE IF NOT EXISTS expenses
      (id TEXT PRIMARY KEY, date TEXT NOT NULL, category TEXT NOT NULL,
       amount_cents INTEGER NOT NULL CHECK(amount_cents>=0), description TEXT NOT NULL,
       updated_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS imported_sales
      (id TEXT PRIMARY KEY, json TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    return conn

def save_expense(payload,db=DB,record_id=None):
    if not isinstance(payload,dict): raise ValueError('資料必須為物件')
    d=valid_date(payload.get('date'))
    category=payload.get('category')
    if category not in CATEGORIES: raise ValueError('未知的費用分類')
    cents=amount(payload.get('amount'))
    description=str(payload.get('description') or '')[:500]
    key=record_id or str(payload.get('id') or uuid.uuid4())
    if len(key)>100 or not key.strip(): raise ValueError('費用 ID 無效')
    with connect(db) as conn:
        if record_id:
            cur=conn.execute('UPDATE expenses SET date=?,category=?,amount_cents=?,description=?,updated_at=? WHERE id=?',
               (d,category,cents,description,datetime.now(timezone.utc).isoformat(),key))
            if cur.rowcount==0: raise LookupError('費用不存在')
        else:
            conn.execute('INSERT INTO expenses VALUES(?,?,?,?,?,?)',(key,d,category,cents,description,datetime.now(timezone.utc).isoformat()))
    return dict(id=key,date=d,category=category,amount=cents/100,description=description,source='sqlite')

def expenses(db=DB):
    with connect(db) as conn:
        rows=conn.execute('SELECT * FROM expenses ORDER BY date DESC,id DESC').fetchall()
    return [dict(id=r['id'],date=r['date'],category=r['category'],amount=r['amount_cents']/100,description=r['description'],source='sqlite') for r in rows]

def delete_expense(key,db=DB):
    with connect(db) as conn:
        cur=conn.execute('DELETE FROM expenses WHERE id=?',(key,))
    if cur.rowcount==0: raise LookupError('費用不存在')

def sync_c1_sales(inventory_db,db=DB):
    """從共用資料庫讀 C1 已完成銷售（含 B1 每份食材成本），寫入 C2。同一筆重複同步會覆蓋，不會重複計算。"""
    if not Path(inventory_db).exists():return 0
    src=sqlite3.connect(str(inventory_db),timeout=10);src.row_factory=sqlite3.Row
    try:
        if not src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='c1_sales'").fetchone():return 0
        sales=[]
        for s in src.execute('SELECT * FROM c1_sales ORDER BY sold_at,id').fetchall():
            items=[dict(productId=i['product_code'],productName=i['product_name'],quantity=i['qty'],unitPrice=i['unit_price'],unitCost=i['unit_cost'])
                   for i in src.execute('SELECT * FROM c1_sale_items WHERE sale_id=? ORDER BY id',(s['id'],))]
            sales.append(dict(id=s['id'],date=s['date'],localTime=s['local_time'],status='completed',source=s['source'],items=items))
    finally:
        src.close()
    with connect(db) as conn:
        for row in sales:
            conn.execute('INSERT INTO imported_sales(id,json,updated_at) VALUES(?,?,datetime("now")) ON CONFLICT(id) DO UPDATE SET json=excluded.json,updated_at=excluded.updated_at',
                         (row['id'],json.dumps(row,ensure_ascii=False)))
    return len(sales)

def adapt_b2_waste(payload):
    """B2 API: {total_loss,records:[{batch_id,quantity,loss,reason,operator,created_at}]}.
    Only sum records once; no use of total_loss to prevent double-counting."""
    if not isinstance(payload,dict) or not isinstance(payload.get('records'),list): raise ValueError('B2 回傳格式不正確')
    out=[]
    for n,row in enumerate(payload['records']):
        stamp=row.get('created_at','')
        d=valid_date(str(stamp)[:10])
        loss=amount(row.get('loss'))/100
        # B2 API lacks original waste record id. An identifier made from row attributes
        # is stable for repeat sync, but identical rows from different events may collide.
        if row.get('id') is not None:
            key=f"b2-{row['id']}"  # B2 已提供報廢流水號
        else:
            signature=json.dumps([row.get(k) for k in ('batch_id','quantity','loss','reason','operator','created_at')],ensure_ascii=False)
            import hashlib
            key='b2-'+hashlib.sha256(signature.encode()).hexdigest()[:20]
        label=str(row.get('reason') or 'B2 報廢')
        if row.get('item_id'):label=f"{row['item_id']}：{label}"
        out.append(dict(id=key,date=d,type='b2-inventory',amount=loss,description=label))
    return out
