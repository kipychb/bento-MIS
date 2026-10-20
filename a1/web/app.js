const $ = id => document.getElementById(id);
let result = null, calculatedInput = null;
const status = (text, error=false) => { $('status').textContent=text; $('status').className=error?'error':''; };
const esc = value => String(value).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, body) {
  const response = await fetch('/api/a1/'+path, body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '請求失敗');
  return data;
}
function invalidate() { result=null; calculatedInput=null; $('save').disabled=true; $('export').disabled=true; $('metrics').replaceChildren(); $('warnings').replaceChildren(); $('forecasts').replaceChildren(); $('purchases').replaceChildren(); }
function input() { return {target:$('target').value,factor:Number($('factor').value),buffer:Number($('buffer').value)/100,data:JSON.parse($('data').value)}; }
function setInput(body) { invalidate(); $('target').value=body.target; $('factor').value=body.factor??1; $('buffer').value=(body.buffer??0.1)*100; $('data').value=JSON.stringify(body.data,null,2); }
function render(r) {
  result=r;
  const count=r.forecasts.reduce((sum,row)=>sum+row.total,0);
  const preorder=r.forecasts.reduce((sum,row)=>sum+row.preorders,0);
  $('metrics').innerHTML=[['建議製作份數',count],['已確認預訂',preorder],['需採購原料品項',r.purchases.filter(p=>p.purchase>0).length]].map(([label,value])=>`<div class="metric">${label}<b>${value}</b></div>`).join('');
  $('warnings').innerHTML=r.warnings.map(w=>`<div class="warning">${esc(w)}</div>`).join('');
  $('forecasts').innerHTML=r.forecasts.map(f=>`<tr><td>${esc(f.name)}<small>${esc(f.slot)}</small></td><td>${f.baseline}</td><td>${f.walk_in}</td><td>${f.preorders}</td><td><strong>${f.total}</strong></td><td>${esc(f.method)}<small>${f.samples} 筆樣本</small></td></tr>`).join('');
  $('purchases').innerHTML=r.purchases.map(p=>`<tr><td>${esc(p.name)}<small>單位：${esc(p.unit)}／包裝 ${p.pack_size}</small></td><td>${p.demand}</td><td>${p.buffer}</td><td>${p.available}</td><td>${p.excluded}</td><td>${p.incoming}</td><td><strong>${p.purchase}</strong></td><td>${p.purchase?p.latest_order_date:'無須下單'}${p.warnings.map(w=>`<small class="error">${esc(w)}</small>`).join('')}</td></tr>`).join('');
  $('save').disabled=false; $('export').disabled=false;
}
async function history() {
  const rows=await api('plans'); $('history').replaceChildren();
  if(!rows.length) {$('history').textContent='尚未儲存計畫';return;}
  rows.forEach(row=>{const button=document.createElement('button');button.className='secondary';button.textContent=`#${row.id} · ${row.target}`;button.onclick=async()=>{try {const saved=await api('plans/'+row.id);setInput(saved.input);render(saved.result);calculatedInput=JSON.stringify(saved.input);status('已載入歷史快照；再次計算將使用今天的計算日期。');} catch(e){status(e.message,true);}};$('history').append(button);});
}
async function loadSample() {setInput(await api('sample'));status('已載入模擬資料，請按「計算需求與採購」。');}
$('form').onsubmit=async event=>{event.preventDefault();invalidate();try{const body=input();const fingerprint=JSON.stringify(body);status('計算中…');const r=await api('calculate',body);if(JSON.stringify(input())!==fingerprint){status('資料已變更，請重新計算。');return;}render(r);calculatedInput=fingerprint;status('計算完成。請確認交期與容量提醒後儲存。');}catch(e){status(e.message,true);}};
$('save').onclick=async()=>{try{const body=input();if(JSON.stringify(body)!==calculatedInput)throw new Error('資料已變更，請重新計算');$('save').disabled=true;const saved=await api('plans',body);status(`已儲存計畫 #${saved.id}；尚未產生採購單。`);await history();}catch(e){status(e.message,true);$('save').disabled=false;}};
function download(data,name){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export').onclick=()=>download(result,'a1-plan-'+result.target+'.json');
$('download').onclick=()=>{try{download(input(),'a1-input.json');}catch(e){status(e.message,true);}};
$('sample').onclick=()=>loadSample().catch(e=>status(e.message,true));
$('import').onchange=async event=>{try{const file=event.target.files[0];if(!file)return;if(file.size>2000000)throw new Error('檔案不可超過 2 MB');const body=JSON.parse(await file.text());if(!body.data||!body.target)throw new Error('請匯入含 target、data 的完整輸入物件');setInput(body);status('資料已匯入，請按計算驗證。');}catch(e){status(e.message,true);}finally{event.target.value='';}};
['target','factor','buffer','data'].forEach(id=>$(id).addEventListener('input',()=>{invalidate();status('資料已變更，請重新計算。');}));
Promise.all([loadSample(),history()]).catch(e=>status(e.message,true));
