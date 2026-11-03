'use strict';
const $=id=>document.getElementById(id);
const controls=['month','category','seller'];
const number=new Intl.NumberFormat('en-US');
const money=new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const percent=value=>value===null?'—':(value*100).toFixed(2)+'%';
let catalog=null,current=null,controller=null,requestId=0,timer=null;
const escapeHTML=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function filters(){return Object.fromEntries(controls.map(id=>[id,$(id).value]));}
function params(){const result=new URLSearchParams();for(const [key,value] of Object.entries(filters()))if(value)result.set(key,value);return result;}
function scopeLabel(){const f=filters(),labels=[];if(f.month)labels.push(f.month);if(f.category)labels.push(catalog.categories.find(x=>x.value===f.category)?.label||f.category);if(f.seller)labels.push('商家 '+f.seller.slice(0,8)+'…');return labels.length?labels.join(' / '):'全部月份 / 全部商品 / 全部商家';}
function metricCards(data){
 const m=data||{};
 const count=key=>data?number.format(m[key]):'—';
 const cards=[
  ['订单量',count('order_count'),data?'全部状态 · 按订单去重':'等待查询',''],
  ['商品成交额 · BRL',data?money.format(m.sales_gmv_cents/100):'—','已送达商品 · 不含运费','accent'],
  ['配送延迟率',data?percent(m.delivery_late_rate):'—',data?`${count('late_order_count')} 延迟 / ${count('delivery_eligible_order_count')} 有效送达`:'晚于预计送达日的订单',''],
  ['低评分订单占比',data?percent(m.low_score_rate):'—',data?`${count('low_score_order_count')} 低评分 / ${count('reviewed_delivered_order_count')} 有效评分`:'已送达订单 · 最新有效评价 1–2 分',''],
  ['已送达状态占比',data?percent(m.delivered_status_share):'—',data?`${count('delivered_order_count')} 已送达 / ${count('order_count')} 订单`:'历史快照占比 · 非最终履约成功率',''],
  ['单商家晚交运代理率',data?percent(m.late_handoff_proxy_rate):'—',data?`${count('single_seller_late_handoff_proxy_count')} 晚交运 / ${count('single_seller_handoff_eligible_count')} 可比订单`:'仅单商家且时间可比订单',''],
 ];
 $('metrics').innerHTML=cards.map((c,index)=>`<article class="metric-card ${c[3]}"><div class="metric-label">${c[0]}<span class="metric-index">0${index+1}</span></div><div class="metric-value">${c[1]}</div><div class="metric-caption">${c[2]}</div></article>`).join('');
 $('metrics').setAttribute('aria-busy',data?'false':'true');
}
function formatCheck(row,value){if(value===null)return 'NULL（无分母 / 不归因）';if(row.key.endsWith('_rate')||row.key==='delivered_status_share')return percent(value);return number.format(value);}
function renderResult(result){
 current=result;metricCards(result.program);
 const ok=result.status==='passed';
 $('status').className='status '+(ok?'passed':'failed');
 $('status').querySelector('.status-icon').textContent=ok?'✓':'!';
 $('status-title').textContent=ok?(result.empty?'没有匹配订单，已通过独立核对':'验证通过 · 两条计算路径结果一致'):'复核未通过 · 存在不一致';
 $('status-detail').textContent=`${result.checks_passed} / ${result.check_count} 项一致 · 原始表重新计算 · ${result.empty?'比率无分母，显示 —':result.comparison_rule}`;
 if(result.ambiguous_review_tie_groups>0)$('status-detail').textContent+=' · 评价并列评分存在冲突，不能判定通过';
 $('duration').textContent=`本次计算 ${(result.elapsed_ms/1000).toFixed(2)} s`;
 $('check-count').textContent=`${result.checks_passed}/${result.check_count}`;
 $('checks-body').innerHTML=result.checks.map(row=>`<tr><td>${escapeHTML(row.label)}</td><td>${escapeHTML(formatCheck(row,row.program))}</td><td>${escapeHTML(formatCheck(row,row.reference))}</td><td><span class="${row.passed?'pass-tag':'fail-tag'}">${row.passed?'✓ 一致':'✕ 不一致'}</span></td></tr>`).join('');
 for(const id of ['download-json','download-sql','copy-sql'])$(id).disabled=false;
 showSQL();
}
function loading(){current=null;metricCards(null);$('status').className='status loading';$('status').querySelector('.status-icon').textContent='↻';$('status-title').textContent='正在实时计算并独立核对';$('status-detail').textContent='同一筛选条件，两条 SQL 在同一数据库快照中执行。';$('duration').textContent='';$('check-count').textContent='—';$('scope').textContent=scopeLabel();$('checks-body').innerHTML='<tr><td colspan="4" class="empty-cell">正在查询数据库并逐项核对…</td></tr>';$('sql-code').textContent='正在生成本次查询的 SQL…';for(const id of ['download-json','download-sql','copy-sql'])$(id).disabled=true;}
async function query(){
 if(!catalog)return;clearTimeout(timer);const id=++requestId;if(controller)controller.abort();controller=new AbortController();loading();$('run').disabled=true;
 try{const response=await fetch('/api/verify?'+params(),{signal:controller.signal});const result=await response.json();if(id!==requestId)return;if(!response.ok)throw new Error(result.error||'查询失败');renderResult(result);}
 catch(error){if(error.name==='AbortError'||id!==requestId)return;$('status').className='status failed';$('status').querySelector('.status-icon').textContent='!';$('status-title').textContent='本次验证未完成';$('status-detail').textContent=error.message;$('checks-body').innerHTML='<tr><td colspan="4" class="empty-cell">没有得到可核验的结果，请重新计算。</td></tr>';$('sql-code').textContent='本次查询未完成。';}
 finally{if(id===requestId)$('run').disabled=false;}
}
function showSQL(){if(current)$('sql-code').textContent=current.sql[$('sql-kind').value];}
function renderSellers(search=''){
 const previous=$('seller').value;const needle=search.trim().toLowerCase();const items=catalog.sellers.filter(s=>!needle||`${s.value} ${s.city} ${s.state}`.toLowerCase().includes(needle)||s.value===previous);
 $('seller').replaceChildren(new Option('全部商家',''));
 for(const s of items)$('seller').add(new Option(`${s.value.slice(0,12)}… · ${s.city} / ${s.state} · ${number.format(s.orders)} 单`,s.value));
 $('seller').value=previous;
}
function reset(shouldQuery=true){for(const key of controls)$(key).value='';$('seller-search').value='';renderSellers();if(shouldQuery)query();}
for(const id of controls)$(id).addEventListener('change',query);
$('seller-search').addEventListener('input',event=>renderSellers(event.target.value));
$('run').addEventListener('click',query);
$('reset').addEventListener('click',()=>reset());
for(const button of document.querySelectorAll('[data-example]'))button.addEventListener('click',()=>{reset(false);if(button.dataset.example==='month')$('month').value='2018-01';if(button.dataset.example==='category')$('category').value='beleza_saude';if(button.dataset.example==='seller')$('seller').value=catalog.sellers[0].value;query();});
for(const tab of document.querySelectorAll('[data-tab]'))tab.addEventListener('click',()=>{for(const other of document.querySelectorAll('[data-tab]')){const active=other===tab;other.setAttribute('aria-selected',String(active));$('panel-'+other.dataset.tab).hidden=!active;}});
$('sql-kind').addEventListener('change',showSQL);
$('copy-sql').addEventListener('click',async()=>{if(!current)return;try{await navigator.clipboard.writeText(current.sql[$('sql-kind').value]);$('copy-sql').textContent='已复制 ✓';setTimeout(()=>$('copy-sql').textContent='复制 SQL',1600);}catch{$('sql-code').focus();const range=document.createRange();range.selectNodeContents($('sql-code'));const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);$('copy-sql').textContent='请按 Ctrl+C 复制';}});
function download(kind){if(!current)return;const query=new URLSearchParams();for(const [key,value] of Object.entries(current.filters))if(value)query.set(key,value);query.set('format',kind);const a=document.createElement('a');a.href='/api/export?'+query;a.download='zhice-verification.'+kind;a.click();}
$('download-json').addEventListener('click',()=>download('json'));
$('download-sql').addEventListener('click',()=>download('sql'));
metricCards(null);
(async()=>{try{const response=await fetch('/api/catalog');if(!response.ok)throw new Error('无法读取数据库选项');catalog=await response.json();for(const m of catalog.months)$('month').add(new Option(`${m.value} · ${number.format(m.orders)} 单`,m.value));for(const c of catalog.categories)$('category').add(new Option(`${c.label} · ${number.format(c.orders)} 单`,c.value));renderSellers();for(const id of [...controls,'seller-search','run'])$(id).disabled=false;document.body.classList.add('ready');$('data-proof').textContent='数据库校验标识 '+catalog.database_sha256.slice(0,12)+' · Olist 完整九表';if(catalog.batch_validation?.status==='passed')$('batch-proof').textContent=`全量预检 ${number.format(catalog.batch_validation.groups)} 组 / ${number.format(catalog.batch_validation.metric_checks)} 项一致`;await query();}catch(error){$('status').className='status failed';$('status-title').textContent='未能连接验证台';$('status-detail').textContent=error.message+'，请确认启动窗口仍在运行。';}})();
