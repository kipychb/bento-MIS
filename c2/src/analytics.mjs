/** C2 core engine: pure, dependency-free, all amounts are TWD. */
export const EXPENSE_CATEGORIES = Object.freeze(['personnel','utilities','rent','other']);
export const CATEGORY_NAMES = Object.freeze({personnel:'人事費',utilities:'水電瓦斯費',rent:'租金',other:'其他營業費用'});
const money = n => Math.round((Number(n) + Number.EPSILON) * 100) / 100;
const num = (v, fallback=0) => v === '' || v == null ? fallback : Number(v);
const nonnegative = (v, name) => {const n = num(v); if (!Number.isFinite(n) || n < 0) throw new Error(`${name}必須為非負數`); return n;};
const dateOnly = value => {
  if (typeof value !== 'string') throw new Error('日期必須為字串');
  const date = value.slice(0,10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`)) || new Date(`${date}T00:00:00Z`).toISOString().slice(0,10) !== date) throw new Error(`日期無效：${value}`);
  return date;
};
export function localDate(value){return dateOnly(value);}
export function assertRange(start,end){const s=dateOnly(start),e=dateOnly(end); if(s>e) throw new Error('起日不可晚於迄日');return {start:s,end:e};}
const inRange=(value,start,end)=>{const d=dateOnly(value);return d>=start&&d<=end;};
const arr=v=>Array.isArray(v)?v:[];
const id=(obj,prefix)=>String(obj.id || `${prefix}-${Date.now()}`);
export function normalizeExpense(raw){
 if(!raw || typeof raw!=='object')throw new Error('費用資料格式錯誤');
 const category=String(raw.category||'');if(!EXPENSE_CATEGORIES.includes(category))throw new Error('費用類別無效（原料採購不可再作營業費用，以免重複計算）');
 const amount=nonnegative(raw.amount,'費用金額');
 return {id:id(raw,'expense'),date:dateOnly(raw.date),category,amount:money(amount),description:String(raw.description||''),source:String(raw.source||'manual')};
}
export function normalizeSale(raw){
 if(!raw || typeof raw!=='object')throw new Error('銷售資料格式錯誤');
 const status=raw.status||'completed';if(!['completed','cancelled','pending'].includes(status))throw new Error('銷售狀態錯誤');
 const items=arr(raw.items).map((item,index)=>{
  const qty=nonnegative(item.quantity,'銷售數量');if(!Number.isInteger(qty)||qty<1) throw new Error('銷售數量須為正整數');
  const unitPrice=nonnegative(item.unitPrice,'單價');
  // Unit cost represents B-group actual per-unit ingredient cost, not purchasing expenditure.
  const unitCost=nonnegative(item.unitCost,'單位食材成本');
  const discount=nonnegative(item.discount||0,'折扣');
  if(discount>money(qty*unitPrice))throw new Error('折扣不可超過品項金額');
  if(!item.productId)throw new Error(`第 ${index+1} 筆缺少品項代號`);
  return {productId:String(item.productId),productName:String(item.productName||item.productId),quantity:qty,unitPrice:money(unitPrice),unitCost:money(unitCost),discount:money(discount)};
 });
 if(!items.length)throw new Error('銷售明細不可為空');
 const refundAmount=nonnegative(raw.refundAmount||0,'退款金額');
 const gross=money(items.reduce((s,i)=>s+i.quantity*i.unitPrice-i.discount,0));
 if(refundAmount>gross)throw new Error('退款金額超過實收銷售額');
 const timestamp=raw.timestamp||`${dateOnly(raw.date)}T12:00:00+08:00`;
 const date=dateOnly(raw.date||timestamp);
 const hhmm=raw.localTime||String(timestamp).match(/T(\d\d:\d\d)/)?.[1]||'12:00';
 if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(hhmm))throw new Error('銷售時間格式錯誤');
 return {id:id(raw,'sale'),date,localTime:hhmm,status,refundAmount:money(refundAmount),items,source:String(raw.source||'pos')};
}
export function normalizeWaste(raw){
 if(!raw||typeof raw!=='object')throw new Error('報廢資料格式錯誤');
 return {id:id(raw,'waste'),date:dateOnly(raw.date),type:String(raw.type||'unknown'),amount:money(nonnegative(raw.amount,'報廢損失')),description:String(raw.description||'')};
}
export function validateDataset(raw){
 if(!raw||typeof raw!=='object')throw new Error('資料集格式錯誤');
 const normalizeUnique=(items,fn,type)=>{const list=arr(items).map(fn);const ids=new Set();for(const x of list){if(ids.has(x.id))throw new Error(`${type}編號重複：${x.id}`);ids.add(x.id)}return list;};
 return {sales:normalizeUnique(raw.sales,normalizeSale,'銷售'),expenses:normalizeUnique(raw.expenses,normalizeExpense,'費用'),waste:normalizeUnique(raw.waste,normalizeWaste,'報廢')};
}
export function periodLabel(time){const hour=Number(time.slice(0,2));return hour<11?'早餐／上午':hour<14?'午餐':hour<17?'下午':hour<21?'晚餐':'深夜';}
export function computeReport(dataset,start,end){
 const range=assertRange(start,end);const data=validateDataset(dataset);
 const sales=data.sales.filter(s=>s.status==='completed'&&inRange(s.date,range.start,range.end));
 const expenses=data.expenses.filter(e=>inRange(e.date,range.start,range.end));
 const waste=data.waste.filter(w=>inRange(w.date,range.start,range.end));
 const productMap=new Map(),timeMap=new Map(),dailyMap=new Map();
 let revenue=0,ingredientCOGS=0,units=0,grossBeforeRefund=0,refunds=0;
 for(const sale of sales){
  const gross=money(sale.items.reduce((sum,i)=>sum+i.quantity*i.unitPrice-i.discount,0));
  const net=money(gross-sale.refundAmount);grossBeforeRefund+=gross; refunds+=sale.refundAmount;revenue+=net;
  const bucket=periodLabel(sale.localTime);
  const t=timeMap.get(bucket)||{period:bucket,quantity:0,orders:0,revenue:0};t.orders++;t.revenue+=net;timeMap.set(bucket,t);
  const d=dailyMap.get(sale.date)||{date:sale.date,orders:0,quantity:0,revenue:0};d.orders++;d.revenue+=net;dailyMap.set(sale.date,d);
  // Allocate order-level refunds across lines in proportion to line revenue.
  for(const item of sale.items){
   const lineGross=money(item.quantity*item.unitPrice-item.discount);
   const lineNet=gross===0?0:lineGross*(1-sale.refundAmount/gross);
   const lineCost=money(item.quantity*item.unitCost);
   units+=item.quantity;ingredientCOGS+=lineCost;t.quantity+=item.quantity;d.quantity+=item.quantity;
   const p=productMap.get(item.productId)||{productId:item.productId,productName:item.productName,quantity:0,revenue:0,ingredientCOGS:0};
   p.quantity+=item.quantity;p.revenue+=lineNet;p.ingredientCOGS+=lineCost;productMap.set(item.productId,p);
  }
 }
 const expensesByCategory=Object.fromEntries(EXPENSE_CATEGORIES.map(c=>[c,0]));
 for(const e of expenses) expensesByCategory[e.category]+=e.amount;
 const operatingExpenses=money(Object.values(expensesByCategory).reduce((a,b)=>a+b,0));
 const wasteLoss=money(waste.reduce((sum,w)=>sum+w.amount,0));
 revenue=money(revenue);ingredientCOGS=money(ingredientCOGS);grossBeforeRefund=money(grossBeforeRefund);refunds=money(refunds);
 const grossProfit=money(revenue-ingredientCOGS);
 const netProfit=money(grossProfit-wasteLoss-operatingExpenses);
 const products=[...productMap.values()].map(p=>({...p,revenue:money(p.revenue),ingredientCOGS:money(p.ingredientCOGS),grossProfit:money(p.revenue-p.ingredientCOGS)})).sort((a,b)=>b.revenue-a.revenue);
 const order=['早餐／上午','午餐','下午','晚餐','深夜'];
 const periods=order.map(period=>({...{period,quantity:0,orders:0,revenue:0},...timeMap.get(period),revenue:money(timeMap.get(period)?.revenue||0)}));
 const days=[...dailyMap.values()].map(d=>({...d,revenue:money(d.revenue)})).sort((a,b)=>a.date.localeCompare(b.date));
 return {range,summary:{orders:sales.length,units,grossBeforeRefund,refunds,revenue,ingredientCOGS,grossProfit,wasteLoss,operatingExpenses,netProfit,grossMarginRate:revenue?money(grossProfit/revenue*100):0,netMarginRate:revenue?money(netProfit/revenue*100):0,averageOrderValue:sales.length?money(revenue/sales.length):0},expensesByCategory:Object.fromEntries(Object.entries(expensesByCategory).map(([k,v])=>[k,money(v)])),products,periods,days,wasteCount:waste.length};
}
/** Forecast interface for A1: daily product quantities and hourly period counts. */
export function toForecastData(dataset,start,end){const r=computeReport(dataset,start,end);return {dateRange:r.range,products:r.products.map(p=>({productId:p.productId,productName:p.productName,soldQuantity:p.quantity})),timePeriods:r.periods.map(p=>({period:p.period,soldQuantity:p.quantity,orderCount:p.orders})),daily:r.days};}
/** Never include raw materials procurement cost as expenses: purchases increase inventory; sold unit cost becomes COGS. */
export function upsertEntry(dataset,kind,raw){const d=validateDataset(dataset);const transforms={sales:normalizeSale,expenses:normalizeExpense,waste:normalizeWaste};if(!transforms[kind])throw new Error('資料類型錯誤');const entry=transforms[kind](raw);return {...d,[kind]:[...d[kind].filter(x=>x.id!==entry.id),entry]};}
export function removeEntry(dataset,kind,entryId){const d=validateDataset(dataset);if(!['sales','expenses','waste'].includes(kind))throw new Error('資料類型錯誤');return {...d,[kind]:d[kind].filter(x=>x.id!==entryId)};}
