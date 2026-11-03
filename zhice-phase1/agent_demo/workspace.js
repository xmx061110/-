/* 四角色工作台：状态只来自后台事件，历史记录直接展示，不播放伪造进度。 */
const iconPaths={
  network:'<rect x="9" y="2" width="6" height="6" rx="1.5"/><rect x="2" y="16" width="6" height="6" rx="1.5"/><rect x="16" y="16" width="6" height="6" rx="1.5"/><path d="M12 8v4M5 16v-4h14v4"/>',
  chart:'<path d="M4 3v17h17M8 15v-4M13 15V7M18 15V4"/>',
  sliders:'<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2" fill="currentColor"/><circle cx="16" cy="12" r="2" fill="currentColor"/><circle cx="8" cy="18" r="2" fill="currentColor"/>',
  shield:'<path d="M12 3 4 6v5c0 5 3 8 8 10 5-2 8-5 8-10V6Z"/><path d="m8 12 3 3 5-6"/>',
  database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 4 16 4 16 0V5M4 12v7c0 4 16 4 16 0v-7"/>',
  package:'<path d="m12 2 9 5v10l-9 5-9-5V7Z M3 7l9 5 9-5M12 12v10M7.5 4.5l9 5"/>',
  target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  wallet:'<path d="M20 7H5a2 2 0 0 1 0-4h13v4M5 7a2 2 0 0 1-2-2v14a2 2 0 0 0 2 2h15V7Z"/><path d="M20 12h-6v5h6"/><circle cx="16" cy="14.5" r=".5"/>',
  play:'<path d="m9 5 11 7-11 7Z"/>',
  search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
  check:'<path d="m5 12 4 4L19 6"/>'
};
function svgIcon(name){return `<svg class="icon" aria-hidden="true" viewBox="0 0 24 24">${iconPaths[name]||iconPaths.network}</svg>`}
document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=svgIcon(el.dataset.icon));
function el(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=String(text);return node}
function iconElement(name,className=''){const node=el('span',className);node.innerHTML=svgIcon(name);return node}
const pages={workspace:'协作工作台',analysis:'经营分析',lab:'算法实验',evidence:'证据与说明'};
function showPage(name,updateHash=true){
  if(!pages[name])name='workspace';
  for(const page of Object.keys(pages))$('#page-'+page).hidden=page!==name;
  document.querySelectorAll('.nav-item').forEach(button=>{const active=button.dataset.page===name;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current')});
  $('#pageCrumb').textContent=pages[name];if(updateHash)history.replaceState(null,'','#'+name);
  if(updateHash)window.scrollTo({top:0,behavior:'instant'});
}
document.querySelectorAll('[data-page]').forEach(button=>button.addEventListener('click',()=>showPage(button.dataset.page)));
window.addEventListener('hashchange',()=>showPage(location.hash.slice(1),false));showPage(location.hash.slice(1),false);
const agentDefinitions=[
  {name:'总控Agent',icon:'network',job:'确认目标，组织协作',input:'用户确认的城市、月日、快递员及决策目标',work:'核对范围，将任务交给经营分析与优化决策',output:'确认目标与固定协作步骤',handoff:'已确认决策目标与协作步骤'},
  {name:'经营分析Agent',icon:'search',job:'读取快照，识别关注点',input:'截至决策时刻的已接单与未完成任务',work:'读取SQL复核后的快照，判断超窗与临近截止状态',output:'当前关注点与快照证据指纹',handoff:'已传递当前任务快照与关注点'},
  {name:'优化决策Agent',icon:'sliders',job:'约束求解，生成关注方案',input:'当前任务、规则优先级及模拟资源预算',work:'调用整数预算工具，选择后续反馈策略',output:'预算内选单与反馈重排策略',handoff:'已提交预算方案与反馈策略'},
  {name:'方案评审Agent',icon:'shield',job:'独立复核，决定是否发布',input:'候选方案、当前快照与程序约束复核结论',work:'检查范围、任务可见性、重复与预算约束',output:'通过或拒绝；失败时不发布方案',handoff:'评审已完成，进入事后回放复核'}
];
const policyNames={static:'不重排',periodic:'周期重排',immediate:'即时重排',stable:'稳定重排'};
const attentionNames={empty:'没有待处理任务',overdue:'存在超窗未完成任务',approaching_deadline:'有任务临近截止',normal:'当前队列正常待处理'};
const statesText={pending:'待命',running:'执行中',complete:'已完成',failed:'失败',rejected:'已拒绝',blocked:'未执行'};
let agentStates=agentDefinitions.map(()=>({state:'pending',stage:null}));
let inspectedAgent=0,currentRecord=null,ladeEvidence=null,ladeCatalog=null,catalogReady=false,catalogSequence=0,busy=false,timelineEvents=[],pendingRequest=null;
const scopeControls=['ladeCity','ladeDay','ladeCourier','ladeStart','ladeDuration','ladeBudget','fourGoal','fourMode'];
function syncControls(){
  for(const id of scopeControls)$('#'+id).disabled=busy||(!catalogReady&&['ladeDay','ladeCourier'].includes(id));
  $('#fourRun').disabled=busy||!catalogReady;$('#ladeRun').disabled=busy||!catalogReady;
  $('#loadSaved').disabled=busy;$('#restoreLast').disabled=busy;
}
function appendTimeline(message,tag='',stamp=null){
  const label=stamp||new Date().toLocaleTimeString('zh-CN',{hour12:false});timelineEvents.push({message,tag,label});
  const list=$('#agentTimeline');if(timelineEvents.length===1)list.replaceChildren();
  const row=el('li');row.append(el('time','',label));if(tag)row.append(el('span','event-tag',tag));row.append(document.createTextNode(message));list.append(row);
  $('#eventCount').textContent=`${timelineEvents.length} 条`;
}
function stageSummary(index){
  const stage=agentStates[index].stage;if(!stage)return agentDefinitions[index].job;
  const output=stage.output;
  if(index===0)return output.goal==='priority'?'目标：优先关注临近截止任务':'目标：减少方案频繁变动';
  if(index===1)return attentionNames[output.attention]||'当前快照已核对';
  if(index===2)return `策略：${policyNames[output.policy]||output.policy}`;
  return output.approved?'约束复核通过，允许发布':'评审拒绝，未发布方案';
}
function renderFlow(){
  const flow=$('#fourStages');flow.replaceChildren();
  agentDefinitions.forEach((definition,index)=>{
    const status=agentStates[index].state;const button=el('button',`agent-node ${status}${inspectedAgent===index?' selected':''}`);
    button.type='button';button.setAttribute('aria-label',`${definition.name}：${statesText[status]}`);button.setAttribute('aria-pressed',String(inspectedAgent===index));
    const top=el('span','node-top');top.append(iconElement(definition.icon,'node-icon'));const badge=el('span','node-state');badge.append(el('i'),document.createTextNode(statesText[status]));top.append(badge);
    const title=el('span','node-name',definition.name);title.append(el('span','node-number',`0${index+1}`));
    button.append(top,title,el('span','node-copy',stageSummary(index)));button.onclick=()=>{inspectedAgent=index;renderFlow()};flow.append(button);
  });
  const count=agentStates.filter(x=>x.state==='complete').length;$('#flowCount').textContent=`${count} / 4 已完成`;renderInspector();
}
function renderInspector(){
  const definition=agentDefinitions[inspectedAgent],status=agentStates[inspectedAgent],stage=status.stage;
  const heading=el('div');heading.append(el('p','inspector-label','当前查看'),el('p','inspector-name',definition.name),el('p','inspector-method',stage?stage.method:status.state==='running'?'后台正在执行此角色':'点击节点查看分工与交接'));
  const content=el('div','inspector-content');let input=definition.input,work=definition.work,output=definition.output;
  if(currentRecord?.result?.snapshot&&inspectedAgent>0){const snapshot=currentRecord.result.snapshot;input=`截至 ${currentRecord.request.start_time}：已接 ${snapshot.accepted_count} 笔，待处理 ${snapshot.active_total} 笔，本轮取 ${snapshot.candidate_count} 笔`}
  if(stage){output=stageSummary(inspectedAgent);if(inspectedAgent===1)output+=`；指纹 ${stage.output.evidence_id.slice(0,12)}…`;if(inspectedAgent===2&&currentRecord?.result?.proposal){const plan=currentRecord.result.proposal.initial_plan;output+=`；关注 ${plan.selected_ids.length} 笔，资源 ${plan.cost_cents/100} 点`}}
  for(const [label,text] of [['输入',input],['处理',work],['输出',output]]){const group=el('div');group.append(el('h4','',label),el('p','',text));content.append(group)}
  $('#agentInspector').replaceChildren(heading,content);
}
function emptyState(target,title,copy,icon='package'){
  const empty=el('div','empty-state');empty.append(iconElement(icon),el('strong','',title),el('p','',copy));target.replaceChildren(empty);
}
function setNumber(id,value,unit){const node=$('#'+id);node.replaceChildren(document.createTextNode(typeof value==='number'?value.toLocaleString('zh-CN'):String(value)));if(unit)node.append(el('small','',unit))}
function resetResults(){
  currentRecord=null;ladeEvidence=null;agentStates=agentDefinitions.map(()=>({state:'pending',stage:null}));inspectedAgent=0;timelineEvents=[];
  $('#fourDownload').disabled=true;$('#ladeDownload').disabled=true;$('#fourEvidence').textContent='请先运行四Agent协作。';$('#ladeEvidence').textContent='请先查询真实任务或运行协作。';
  $('#ladeMetrics').textContent='';$('#ladeLimits').textContent='';$('#ladeTable').replaceChildren();$('#ladeOverview').replaceChildren();$('#ladeOverview').hidden=true;
  $('#taskList').replaceChildren();$('#historicalFacts').replaceChildren();$('#recordLabel').textContent='运行后可下载角色输入输出、原始SQL与逐轮回放记录。';
  $('#metricActiveLabel').textContent='当前待处理任务';setNumber('metricActive','—','笔');setNumber('metricSelected','—','笔');setNumber('metricBudget','—','点');
  $('#metricActiveNote').textContent='运行后读取当前快照';$('#metricSelectedNote').textContent='评审通过后生成';$('#metricBudgetNote').textContent='资源费用不代表真实成本';$('#metricReview').textContent='待运行';$('#metricReviewNote').textContent='原始SQL / 可见性 / 预算';
  $('#proposalBadge').textContent='等待评审';$('#proposalBadge').className='badge neutral';$('#fourStatus').classList.remove('error-text');
  emptyState($('#fourResult'),'等待生成关注建议','设定决策范围，运行四Agent协作后查看经过评审的关注方案。','target');
  emptyState($('#policyChart'),'等待回放结果','完成协作或仅查询回放后，对比四种反馈策略。','chart');
  $('#agentTimeline').replaceChildren(el('li','','运行后显示后台真实执行事件。'));$('#eventCount').textContent='0 条';renderFlow();
}
function updateTimeSummary(){$('#scopeTimeSummary').textContent=`${$('#ladeStart').value||'—'} 起 · ${$('#ladeDuration').value} 分钟`}
async function loadLaDe(city='jl',day=null){
  const sequence=++catalogSequence;catalogReady=false;resetResults();syncControls();$('#ladeStatus').textContent='正在读取所选范围……';
  try{
    const query=new URLSearchParams({city});if(day)query.set('day',day);
    const response=await fetch('/api/lade/catalog?'+query);const data=await response.json();if(!response.ok)throw Error(data.error);
    if(sequence!==catalogSequence)return;ladeCatalog=data;
    if(!$('#ladeCity').options.length)for(const row of data.cities){const option=el('option','',row.label);option.value=row.value;$('#ladeCity').append(option)}$('#ladeCity').value=data.city;
    $('#ladeDay').replaceChildren();for(const value of data.days){const option=el('option','',value);option.value=value;$('#ladeDay').append(option)}$('#ladeDay').value=data.day;
    $('#ladeCourier').replaceChildren();for(const row of data.couriers){const option=el('option','',`${row.courier_id} · 当日${row.valid_tasks}笔`);option.value=row.courier_id;$('#ladeCourier').append(option)}
    catalogReady=data.couriers.length>0;if(catalogReady)$('#ladeStart').value=data.couriers[0].start_time;
    $('#ladeStatus').textContent=catalogReady?`数仓目录已载入 · ${data.days.length}个数据月日 · 快递员可自行选择`:'所选范围没有有效任务';
    $('#connectionStatus').classList.add('ready');$('#connectionStatus').replaceChildren(el('i'),document.createTextNode('真实数仓已连接'));updateTimeSummary();
  }catch(error){if(sequence===catalogSequence){$('#ladeStatus').textContent=error.message;$('#connectionStatus').replaceChildren(el('i'),document.createTextNode('数仓连接失败'))}}
  finally{if(sequence===catalogSequence)syncControls()}
}
$('#ladeCity').onchange=()=>loadLaDe($('#ladeCity').value);
$('#ladeDay').onchange=()=>loadLaDe($('#ladeCity').value,$('#ladeDay').value);
for(const id of scopeControls){$('#'+id).addEventListener('input',()=>{if(busy)return;resetResults();$('#fourStatus').textContent='条件已更改，重新运行后生成本次方案。';updateTimeSummary()})}
$('#ladeCourier').onchange=()=>{const row=ladeCatalog?.couriers.find(x=>x.courier_id===$('#ladeCourier').value);if(row)$('#ladeStart').value=row.start_time;updateTimeSummary()};
fetch('/api/four-agents/config').then(r=>{if(!r.ok)throw Error();return r.json()}).then(config=>{
  if(!busy&&!currentRecord)$('#fourMode').value=config.model_configured?'model':'tools';
  $('#fourConfig').textContent=config.model_configured?`${config.model} 已就绪 · 真实协作每次调用4个角色`:'模型尚未配置 · 可选择仅工具演练';
}).catch(()=>$('#fourConfig').textContent='模型配置读取失败，请检查本地服务');
function readRequest(){
  if(!catalogReady)throw Error('请等待数据目录载入完成。');const budget=$('#ladeBudget').value;
  if(!/^\d+$/.test(budget)||Number(budget)>5000)throw Error('模拟资源预算须为0到5000的整数。');
  return {city:$('#ladeCity').value,day:$('#ladeDay').value,courier_id:$('#ladeCourier').value,start_time:$('#ladeStart').value,
    duration_minutes:Number($('#ladeDuration').value),budget_points:Number(budget),goal:$('#fourGoal').value,mode:$('#fourMode').value};
}
function handleProgress(event){
  if(event.type==='stage_started'){
    const index=event.index;agentStates[index].state='running';inspectedAgent=index;renderFlow();
    $('#fourStatus').textContent=`${event.role}正在执行 · ${pendingRequest.mode==='model'?'真实模型调用':'确定性工具演练'}`;
    appendTimeline('开始执行',event.role);
  }else if(event.type==='stage_completed'){
    const index=event.index;agentStates[index]={state:index===3&&!event.stage.output.approved?'rejected':'complete',stage:event.stage};renderFlow();
    appendTimeline(stageSummary(index),event.stage.role);
  }else if(event.type==='verification_started'){
    $('#metricReview').textContent='复核中';$('#fourStatus').textContent='四角色已完成交接 · 正在独立复核事后回放';appendTimeline('策略已确定，开始历史回放与原始表复核','独立验证');
  }else if(event.type==='error'){throw Error(event.message)}
}
async function streamRun(request){
  const response=await fetch('/api/four-agents/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
  if(!response.ok){const problem=await response.json();throw Error(problem.error||'协作请求失败')}
  if(!response.body)throw Error('当前浏览器无法读取进度流。');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',record=null;
  const consume=line=>{if(!line.trim())return;const event=JSON.parse(line);if(event.type==='result')record=event.record;else handleProgress(event)};
  try{for(;;){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let end;while((end=buffer.indexOf('\n'))>=0){consume(buffer.slice(0,end));buffer=buffer.slice(end+1)}if(done)break}if(buffer.trim())consume(buffer)}finally{reader.releaseLock()}
  if(!record)throw Error('协作连接中断，尚未收到完整复核结果。');return record;
}
function timeOfDay(seconds){const value=((seconds%86400)+86400)%86400;return `${String(Math.floor(value/3600)).padStart(2,'0')}:${String(Math.floor(value%3600/60)).padStart(2,'0')}`}
function renderTasks(result){
  const plan=result.proposal.initial_plan;const items=new Map(result.snapshot.items.map(x=>[x.order_id,x]));const ids=plan.selected_ids;let expanded=false;
  function draw(){
    const target=$('#taskList');target.replaceChildren();if(!ids.length){target.append(el('p','decision-sub','本次未分配关注任务，资源支出为0点。'));return}
    const wrap=el('div','table-scroll');const table=el('table','task-table');const head=el('thead'),tr=el('tr');
    for(const title of ['任务编号','截止时刻','资源点','建议'])tr.append(el('th','',title));head.append(tr);table.append(head);const body=el('tbody');
    for(const id of ids.slice(0,expanded?ids.length:5)){const item=items.get(id),row=el('tr');const key=el('td','task-id',`#${item.source_task_id}`);key.title=id;
      const status=el('td');status.append(el('span','task-status','优先关注'));row.append(key,el('td','',timeOfDay(item.window_end_s)),el('td','',item.cost_cents/100),status);body.append(row)}
    table.append(body);wrap.append(table);target.append(wrap);
    if(ids.length>5){const button=el('button','text-button task-toggle',expanded?'收起任务名单 ↑':`展开全部 ${ids.length} 笔任务 ↓`);button.onclick=()=>{expanded=!expanded;draw()};target.append(button)}
  }draw();
}
function renderComparison(replay,chosen=null){
  const chart=el('div','policy-chart'),max=Math.max(1,...replay.results.map(x=>x.utility_units));
  for(const result of replay.results){const row=el('div',`policy-row${result.policy===chosen?' chosen':''}`),track=el('div','bar-track'),bar=el('div','bar-fill');bar.style.width=`${Math.max(0,result.utility_units)/max*100}%`;track.append(bar);
    const number=el('span','bar-value',Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:1}).format(result.utility_units));number.title=`规则效用 ${result.utility_units.toLocaleString('zh-CN')}；资源 ${result.spent_cents/100} 点；方案增删 ${result.plan_edit_count} 项`;
    row.append(el('span','policy-label',policyNames[result.policy]),track,number);row.setAttribute('aria-label',`${policyNames[result.policy]}，规则效用${result.utility_units}，支出${result.spent_cents/100}点`);chart.append(row)}
  const target=$('#policyChart');target.replaceChildren(chart);if(chosen){const legend=el('p','policy-meta');legend.append(el('span','legend-box'),document.createTextNode(`本次采用：${policyNames[chosen]} · 在回放前选定`));target.append(legend)}
  const facts=replay.historical_metrics.metrics;const factCard=el('div','history-card');factCard.append(el('h3','','全日历史核对 · 仅供事后参考'));
  const rate=facts.pickup_late_rate===null?'—':(facts.pickup_late_rate*100).toFixed(2)+'%';const value=el('div','history-numbers',rate);value.append(el('small','',`${facts.pickup_late_count} / ${facts.window_eligible} 笔超窗`));factCard.append(value,el('p','','该历史比例未作为模型提前选策略的依据。'));$('#historicalFacts').replaceChildren(factCard);
}
function saveReplayEvidence(replay){
  ladeEvidence=replay;$('#ladeDownload').disabled=false;$('#ladeEvidence').textContent=JSON.stringify(replay,null,2);$('#ladeLimits').textContent=replay.limitations.join('\n');$('#ladeMetrics').textContent=JSON.stringify(replay.historical_metrics.metrics,null,2);
  const table=el('table');const head=el('tr');for(const title of ['策略','规则效用','资源点','行动数','方案增删'])head.append(el('th','',title));table.append(head);
  for(const row of replay.results){const tr=el('tr');for(const value of [policyNames[row.policy],row.utility_units,row.spent_cents/100,row.executed_count,row.plan_edit_count])tr.append(el('td','',value));table.append(tr)}$('#ladeTable').replaceChildren(table);
}
function displayRecord(record,restored=false){
  if(record.record_verification!=='passed'||!record.result)throw Error('运行记录尚未完成或指纹复核失败。');currentRecord=record;const result=record.result;
  agentStates=agentDefinitions.map((_,index)=>{const stage=result.stages[index];return {state:stage?(index===3&&!stage.output.approved?'rejected':'complete'):(result.status==='failed'&&index===result.stages.length?'failed':'blocked'),stage:stage||null}});
  inspectedAgent=Math.min(result.stages.length,3);renderFlow();$('#fourEvidence').textContent=JSON.stringify(record,null,2);$('#fourDownload').disabled=false;
  $('#recordLabel').textContent=`运行编号 ${record.run_id} · 记录指纹复核通过`;
  const mode=result.mode==='model'?'真实模型协作':'仅工具演练';$('#fourStatus').textContent=`${restored?'已载入历史记录 · ':''}${mode}${result.status==='passed'?'完成':result.status==='review_rejected'?'评审拒绝':'失败'} · 模型调用 ${result.model_calls} 次 · 记录已保存`;
  if(restored){timelineEvents=[];$('#agentTimeline').replaceChildren();let seq=0;for(const event of record.events){const payload=event.payload;if(payload.kind==='role_input')appendTimeline('开始执行',payload.role,`记录 ${String(++seq).padStart(2,'0')}`);if(payload.kind==='role_output')appendTimeline('输出已通过结构校验',payload.role,`记录 ${String(++seq).padStart(2,'0')}`)}}else appendTimeline(result.status==='passed'?'原始SQL、预算和逐轮回放复核通过':'未发布建议，运行记录可下载','运行结束');
  try{localStorage.setItem('zhice.lastRun',record.run_id);$('#restoreLast').hidden=false}catch{}
  $('#savedRunId').value=record.run_id;
  if(result.snapshot){setNumber('metricActive',result.snapshot.active_total,'笔');$('#metricActiveNote').textContent=`截至 ${result.request.start_time} · 本轮取${result.snapshot.candidate_count}笔`}
  if(result.status!=='passed'){
    $('#proposalBadge').textContent='未发布';$('#metricReview').textContent=result.status==='review_rejected'?'已拒绝':'未通过';$('#metricReviewNote').textContent='未生成可采用的方案';$('#fourStatus').classList.add('error-text');
    emptyState($('#fourResult'),'本次未发布关注方案',result.error?.message||'方案评审拒绝，请查看记录后重新核对。','shield');return;
  }
  const plan=result.proposal.initial_plan;setNumber('metricSelected',plan.selected_ids.length,'笔');setNumber('metricBudget',plan.cost_cents/100,'点');$('#metricSelectedNote').textContent=`策略：${policyNames[result.proposal.policy]}`;$('#metricBudgetNote').textContent=`预算 ${result.request.budget_points} 点 · 剩余 ${result.request.budget_points-plan.cost_cents/100} 点`;$('#metricReview').textContent='已通过';$('#metricReviewNote').textContent='原始SQL与预算记账一致';$('#proposalBadge').textContent='评审通过';$('#proposalBadge').className='badge green-light';
  const highlight=el('div','decision-highlight',`优先关注 ${plan.selected_ids.length} 笔任务`);highlight.append(el('small','',`/ 当前队列 ${result.snapshot.active_total} 笔`));
  $('#fourResult').replaceChildren(highlight,el('p','decision-sub',`${result.request.day} · 快递员 ${result.request.courier_id} · ${result.request.start_time} 起`),el('p','',`在 ${result.request.budget_points} 点模拟资源预算内，按截止时间规则分配关注任务。采用${policyNames[result.proposal.policy]}，后续状态变化由算法处理。`));
  renderTasks(result);renderComparison(result.replay,result.proposal.policy);saveReplayEvidence(result.replay);
}
$('#fourRun').onclick=async()=>{
  try{
    pendingRequest=readRequest();busy=true;resetResults();syncControls();$('#metricReview').textContent='待评审';$('#fourStatus').textContent='正在核对范围，即将启动四角色协作……';appendTimeline('已确认范围，正在准备协作','任务提交');
    $('#fourAgentsSection').scrollIntoView({block:'center',behavior:'instant'});
    const record=await streamRun(pendingRequest);displayRecord(record);
  }catch(error){
    const running=agentStates.findIndex(x=>x.state==='running');if(running>=0)agentStates[running].state='failed';renderFlow();$('#fourStatus').textContent=error.message;$('#fourStatus').classList.add('error-text');appendTimeline(error.message,'运行异常');
  }finally{busy=false;syncControls()}
};
$('#ladeRun').onclick=async()=>{
  try{const request=readRequest();busy=true;resetResults();syncControls();$('#ladeStatus').textContent='正在查询真实历史任务并独立复核……';delete request.goal;delete request.mode;const replay=await api('/api/lade/replay',request);
    if(replay.verification.raw_source_check!=='passed'||replay.historical_metrics.status!=='passed')throw Error('原始表复核失败，未展示结果。');
    saveReplayEvidence(replay);renderComparison(replay);$('#metricActiveLabel').textContent='全日有效任务';setNumber('metricActive',replay.historical_metrics.metrics.eligible,'笔');$('#metricActiveNote').textContent=`回放范围内候选 ${replay.candidate_count} 笔`;$('#metricReview').textContent='已通过';$('#metricReviewNote').textContent='原始表SQL与事件回放一致';
    $('#ladeStatus').textContent=`${replay.city_label} ${replay.day} · 快递员${replay.courier_id} · 原始SQL与逐轮复核通过`;
    $('#fourStatus').textContent='已完成独立工具回放，本次未启动智能体协作。';$('#ladeOverview').hidden=false;$('#ladeOverview').append(el('p','decision-sub',`本次仅查询真实任务并比较策略。回放候选 ${replay.candidate_count}/${replay.eligible_scope_tasks} 笔；模拟资源预算 ${replay.budget_points} 点。`));
  }catch(error){$('#ladeStatus').textContent=error.message}finally{busy=false;syncControls()}
};
function downloadJson(value,name){if(!value)return;const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
$('#fourDownload').onclick=()=>downloadJson(currentRecord,`zhice-four-agents-${currentRecord?.run_id}.json`);$('#ladeDownload').onclick=()=>downloadJson(ladeEvidence,'zhice-lade-evidence.json');
async function restoreRecord(id){
  if(!/^[a-f0-9]{32}$/.test(id||'')){$('#savedStatus').textContent='请输入32位有效运行编号。';return}
  try{busy=true;syncControls();$('#savedStatus').textContent='正在读取已保存记录……';const response=await fetch('/api/four-agents/runs/'+id),record=await response.json();if(!response.ok)throw Error(record.error);if(!record.result)throw Error('该次运行尚未结束。');
    const request=record.request;await loadLaDe(request.city,request.day);if(!catalogReady)throw Error('记录对应的数据范围不可用。');
    $('#ladeCourier').value=request.courier_id;$('#ladeStart').value=request.start_time;$('#ladeDuration').value=request.duration_minutes;$('#ladeBudget').value=request.budget_points;$('#fourGoal').value=request.goal;$('#fourMode').value=request.mode;updateTimeSummary();displayRecord(record,true);$('#savedStatus').textContent='已读取并复核历史记录，当前显示该次运行的筛选条件与结果。';showPage('workspace');
  }catch(error){$('#savedStatus').textContent=error.message}finally{busy=false;syncControls()}
}
$('#loadSaved').onclick=()=>restoreRecord($('#savedRunId').value.trim());$('#restoreLast').onclick=()=>{try{restoreRecord(localStorage.getItem('zhice.lastRun'))}catch{}};
try{$('#restoreLast').hidden=!localStorage.getItem('zhice.lastRun')}catch{}
resetResults();loadLaDe();
