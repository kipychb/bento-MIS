"""A1 pure calculation module. Quantities use each ingredient's base unit."""
from datetime import date, timedelta
import math


def number(value, label, integer=False, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} 必須是有限數字')
    if value < 0 or (positive and value <= 0) or (integer and value != int(value)):
        raise ValueError(f'{label} 必須是{"正" if positive else "非負"}{"整數" if integer else "數字"}')
    return value


def day(value):
    if not isinstance(value, str):
        raise ValueError('日期必須是 YYYY-MM-DD 字串')
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError('日期格式必須為 YYYY-MM-DD')
    return result


def calculate(data, target, factor=1.0, buffer=0.1, as_of=None):
    target = day(target)
    as_of = day(as_of) if as_of else date.today()
    number(factor, '特殊日期倍率', positive=True)
    number(buffer, '原料緩衝比例')
    if factor > 3 or buffer > 1:
        raise ValueError('倍率上限為 3，緩衝比例上限為 1')
    if target < as_of:
        raise ValueError('計畫日期不可早於計算日期')
    if not isinstance(data, dict):
        raise ValueError('資料必須是 JSON 物件')
    for key in ('products', 'ingredients', 'sales', 'preorders', 'stock', 'incoming'):
        if not isinstance(data.get(key), list):
            raise ValueError(f'{key} 必須是陣列')
    def indexed(rows, label):
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
                raise ValueError(f'{label} 需有非空字串 id')
            if row['id'] in result:
                raise ValueError(f'{label} id 重複')
            result[row['id']] = row
        return result
    products = indexed(data['products'], '商品')
    ingredients = indexed(data['ingredients'], '原料')
    if not products or not ingredients:
        raise ValueError('至少需要一個商品及原料')
    for i in ingredients.values():
        if not isinstance(i.get('name'), str) or not isinstance(i.get('unit'), str):
            raise ValueError('原料需要 name 和 unit')
        number(i['pack_size'], '包裝量', positive=True)
        number(i['lead_days'], '交期', integer=True)
        number(i['capacity'], '容量')
    for p in products.values():
        if not isinstance(p.get('name'), str) or not isinstance(p.get('recipe'), dict) or not p['recipe']:
            raise ValueError('商品需要名稱與非空食譜')
        for ingredient, qty in p['recipe'].items():
            if ingredient not in ingredients:
                raise ValueError(f'食譜原料不存在：{ingredient}')
            number(qty, '每份原料用量', positive=True)
    slots = data.get('slots')
    if not isinstance(slots, list) or not slots or any(not isinstance(s, str) or not s for s in slots) or len(set(slots)) != len(slots):
        raise ValueError('slots 必須為不重複的時段字串陣列')
    seen = set()
    for s in data['sales']:
        key = (s['date'], s['product_id'], s['slot'])
        day(s['date'])
        if key in seen or s['product_id'] not in products or s['slot'] not in slots:
            raise ValueError('銷售紀錄重複，或商品／時段不存在')
        seen.add(key)
        number(s['walk_in_qty'], '現場銷量', integer=True)
    for order in data['preorders']:
        day(order['date'])
        if order['product_id'] not in products or order['slot'] not in slots or order['status'] not in ('confirmed', 'cancelled', 'fulfilled'):
            raise ValueError('預訂商品、時段或狀態錯誤')
        number(order['qty'], '預訂量', integer=True)
    for group in ('stock', 'incoming'):
        for row in data[group]:
            if row['ingredient_id'] not in ingredients:
                raise ValueError('庫存原料不存在')
            day(row['expires_on'])
            number(row['qty'], '原料數量')
            if group == 'stock':
                number(row['reserved_qty'], '已保留原料')
                if row['reserved_qty'] > row['qty']:
                    raise ValueError('已保留量不可大於庫存')
            else:
                day(row['arrival_date'])
                if row['status'] not in ('confirmed', 'cancelled', 'received'):
                    raise ValueError('到貨狀態錯誤')
    warnings, forecasts = [], []
    requirements = dict.fromkeys(ingredients, 0.0)
    for pid, product in products.items():
        for slot in slots:
            history = sorted([s for s in data['sales'] if s['product_id'] == pid and s['slot'] == slot
                              and target - timedelta(days=28) <= day(s['date']) < min(target, as_of)], key=lambda s: s['date'])
            same = [s for s in history if day(s['date']).weekday() == target.weekday()]
            chosen = same if same else history[-7:]
            method = '同星期加權平均' if same else '最近七筆加權平均'
            if not chosen:
                warnings.append(f'{product["name"]}／{slot} 無歷史資料，僅計入預訂，請人工評估')
                method = '無歷史資料'
            elif len(chosen) < 3:
                warnings.append(f'{product["name"]}／{slot} 僅 {len(chosen)} 筆樣本，預測可信度有限')
            mean = sum((n+1)*s['walk_in_qty'] for n,s in enumerate(chosen)) / sum(range(1,len(chosen)+1)) if chosen else 0
            walk_in = math.ceil(round(mean * factor, 10))
            orders = sum(o['qty'] for o in data['preorders'] if o['date'] == target.isoformat() and o['product_id'] == pid and o['slot'] == slot and o['status'] == 'confirmed')
            total = walk_in + orders
            forecasts.append(dict(product_id=pid, name=product['name'], slot=slot, baseline=round(mean,2), walk_in=walk_in, preorders=orders, total=total, samples=len(chosen), method=method))
            for iid, qty in product['recipe'].items():
                requirements[iid] += qty * total
    purchases = []
    for iid, ingredient in ingredients.items():
        # expires_on is an exclusive cutoff: the batch is unusable on that date.
        usable = sum(r['qty']-r['reserved_qty'] for r in data['stock'] if r['ingredient_id']==iid and day(r['expires_on'])>target)
        expired = sum(r['qty']-r['reserved_qty'] for r in data['stock'] if r['ingredient_id']==iid and day(r['expires_on'])<=target)
        incoming = sum(r['qty'] for r in data['incoming'] if r['ingredient_id']==iid and r['status']=='confirmed' and as_of<=day(r['arrival_date'])<=target and day(r['expires_on'])>target)
        demand = requirements[iid]
        reserve = demand * buffer
        shortage = max(0, round(demand + reserve - usable - incoming, 8))
        purchase = math.ceil(round(shortage / ingredient['pack_size'], 10)) * ingredient['pack_size']
        latest = target - timedelta(days=ingredient['lead_days'])
        flags = []
        if purchase and latest < as_of:
            flags.append('一般交期來不及，需人工協調')
        if usable + incoming + purchase > ingredient['capacity']:
            flags.append('預計到貨後超過容量，需分批到貨／調整計畫')
        purchases.append(dict(ingredient_id=iid, name=ingredient['name'], unit=ingredient['unit'], demand=round(demand,4), buffer=round(reserve,4), available=round(usable,4), excluded=round(expired,4), incoming=round(incoming,4), shortage=shortage, purchase=round(purchase,4), pack_size=ingredient['pack_size'], latest_order_date=latest.isoformat(), warnings=flags))
    return dict(target=target.isoformat(), as_of=as_of.isoformat(), factor=factor, buffer_ratio=buffer, forecasts=forecasts, purchases=purchases, warnings=warnings)
