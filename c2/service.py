"""C2 expense management (SQLite), B2 waste adapter and import snapshots. Standard library only."""
from __future__ import annotations
import json
import sqlite3
import uuid
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

def connect(path=DB):
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
        signature=json.dumps([row.get(k) for k in ('batch_id','quantity','loss','reason','operator','created_at')],ensure_ascii=False)
        import hashlib
        digest=hashlib.sha256(signature.encode()).hexdigest()[:20]
        out.append(dict(id=f'b2-{digest}',date=d,type='b2-inventory',amount=loss,description=str(row.get('reason') or 'B2 報廢')))
    return out
