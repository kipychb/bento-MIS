import {validateDataset} from './analytics.mjs';
/** Exact interface A1 sample().data.sales consumes; excludes preorders. */
export function toA1Sales(dataset,start,end){
 const {sales}=validateDataset(dataset);const sums=new Map();
 const slotOf=t=>t<'11:00'?'早餐':t<'17:00'?'午餐':t<'21:00'?'晚餐':'宵夜';
 for(const sale of sales){if(sale.status!=='completed'||sale.date<start||sale.date>end||sale.source==='preorder')continue;
  const slot=slotOf(sale.localTime);
  for(const i of sale.items){const key=JSON.stringify([sale.date,i.productId,slot]);sums.set(key,(sums.get(key)||0)+i.quantity);}}
 return [...sums].map(([key,walk_in_qty])=>{const [date,product_id,slot]=JSON.parse(key);return {date,product_id,slot,walk_in_qty};}).sort((a,b)=>a.date.localeCompare(b.date)||a.product_id.localeCompare(b.product_id)||a.slot.localeCompare(b.slot));
}
export function adaptB2(payload){if(!payload||!Array.isArray(payload.records))throw new Error('B2 格式錯誤');return payload.records.map(r=>({id:String(r.id||`b2-${r.batch_id}-${r.created_at}`),date:String(r.date||r.created_at).slice(0,10),type:'b2',amount:Number(r.amount??r.loss),description:r.description||r.reason||''}));}
