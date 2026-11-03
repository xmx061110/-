"""一次性改版：保留既有实验功能，组织为独立工作区。"""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
old=(ROOT/'work/frontend-before-redesign/index.html').read_text(encoding='utf8')
sections=re.findall(r'<section\b.*?</section>',old,re.S)
assert len(sections)==11
def legacy(index):
    content=re.sub(r'(<h2>)[①-⑪]\s*',r'\1',sections[index])
    return content.replace('<section>','<section class="panel legacy-panel">',1).replace('<section id=', '<section class="panel legacy-panel" id=',1)

html='''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="theme-color" content="#193d33"><title>智策 · 物流决策工作台</title>
  <link rel="stylesheet" href="/workspace.css">
</head>
<body>
<a class="skip-link" href="#main">跳到主要内容</a>
<aside class="sidebar">
  <a class="brand" href="#workspace" aria-label="智策首页"><span class="brand-mark" aria-hidden="true">策</span><span>智策<small>ZHICE / DECISION OS</small></span></a>
  <p class="nav-label">工作空间</p>
  <nav aria-label="主导航">
    <button class="nav-item active" data-page="workspace" aria-current="page"><span data-icon="network"></span>协作工作台<span class="nav-arrow">↗</span></button>
    <button class="nav-item" data-page="analysis"><span data-icon="chart"></span>经营分析</button>
    <button class="nav-item" data-page="lab"><span data-icon="sliders"></span>算法实验</button>
    <button class="nav-item" data-page="evidence"><span data-icon="shield"></span>证据与说明</button>
  </nav>
  <div class="sidebar-note"><span class="small-dot"></span>本地工作空间<p>从真实数据出发<br>让决策过程可以复核</p></div>
  <div class="sidebar-bottom"><span class="avatar">Z</span><span>智策项目组<small>本地原型 · v0.3</small></span><span class="local-dot"></span></div>
</aside>
<div class="app-shell">
  <header class="topbar"><div class="breadcrumb">工作空间 <span>/</span> <b id="pageCrumb">协作工作台</b></div><div class="topbar-right"><span class="connection" id="connectionStatus"><i></i>正在连接数仓</span><span class="topbar-divider"></span><span class="local-label">LOCAL</span></div></header>
  <main id="main">
  <div class="page" id="page-workspace">
    <div class="page-heading"><div><div class="eyebrow">MULTI-AGENT WORKSPACE</div><h1>协作决策工作台<span class="title-dot">.</span></h1><p>选定任务范围，让四个智能体共同生成可复核的履约建议。</p></div><span class="badge neutral"><span data-icon="database"></span>LaDe 真实历史数据</span></div>
    <section class="panel scope-panel" id="ladeSection" aria-labelledby="scopeTitle">
      <div class="section-title"><div><span class="section-index">01</span><h2 id="scopeTitle">设定决策范围</h2></div><span class="micro-label">历史回放 · 资源预算为模拟</span></div>
      <div class="filters scope-primary">
        <label>城市<select id="ladeCity" aria-label="LaDe城市"></select></label>
        <label>数据月日 <span class="field-hint">年份未知</span><select id="ladeDay" aria-label="数据月日（年份未知）"></select></label>
        <label>快递员<select id="ladeCourier"></select></label>
      </div>
      <div class="filters scope-secondary">
        <label>决策目标<select id="fourGoal" aria-label="协作决策目标"><option value="priority">优先关注临近截止的任务</option><option value="stability">减少方案频繁变动</option></select></label>
        <label>模拟资源预算 <span class="field-hint">点</span><input id="ladeBudget" type="number" min="0" max="5000" step="1" value="50" aria-label="模拟资源预算（点）"></label>
        <label>运行方式<select id="fourMode" aria-label="四Agent运行方式"><option value="model">真实模型协作</option><option value="tools">仅工具演练 · 不调用模型</option></select></label>
      </div>
      <div class="scope-bottom"><details class="advanced"><summary>回放设置<span id="scopeTimeSummary">选择快递员后载入</span></summary><div class="filters"><label>回放开始时刻<input id="ladeStart" type="time" value="09:00"></label><label>回放时长<select id="ladeDuration"><option value="60">60分钟</option><option value="120" selected>120分钟</option><option value="180">180分钟</option></select></label></div></details><div class="button-group"><button class="button secondary" id="ladeRun" disabled>仅查询与回放</button><button class="button primary" id="fourRun" disabled><span data-icon="play"></span>开始四Agent协作</button></div></div>
      <div class="scope-meta"><p id="ladeStatus" role="status">正在读取真实数据库目录……</p><p id="fourConfig">正在检查模型配置……</p></div>
    </section>
    <div class="kpi-grid" aria-label="本次决策概览">
      <article class="kpi"><span id="metricActiveLabel">当前待处理任务</span><strong id="metricActive">—<small>笔</small></strong><p id="metricActiveNote">运行后读取当前快照</p><span class="kpi-icon" data-icon="package"></span></article>
      <article class="kpi"><span>建议关注任务</span><strong id="metricSelected">—<small>笔</small></strong><p id="metricSelectedNote">评审通过后生成</p><span class="kpi-icon" data-icon="target"></span></article>
      <article class="kpi"><span>模拟资源使用</span><strong id="metricBudget">—<small>点</small></strong><p id="metricBudgetNote">资源费用不代表真实成本</p><span class="kpi-icon" data-icon="wallet"></span></article>
      <article class="kpi verification-kpi"><span>独立复核</span><strong id="metricReview">待运行</strong><p id="metricReviewNote">原始SQL / 可见性 / 预算</p><span class="kpi-icon" data-icon="shield"></span></article>
    </div>
    <section class="panel collaboration-panel" id="fourAgentsSection" aria-labelledby="collabTitle">
      <div class="section-title"><div><span class="section-index">02</span><h2 id="collabTitle">四智能体协作</h2><span class="badge green-light">可追溯流程</span></div><span class="flow-count" id="flowCount">0 / 4 已完成</span></div>
      <p class="section-description" id="fourStatus" role="status" aria-live="polite">等待开始。点击任一智能体，可查看它的职责与交接内容。</p>
      <div class="agent-flow" id="fourStages" aria-label="四智能体协作流程"></div>
      <div class="flow-handoffs" aria-hidden="true"><span>确认目标与范围</span><span>传递当前快照</span><span>提交预算方案</span><span>复核后发布</span></div>
      <div class="agent-inspector" id="agentInspector"></div>
      <details class="timeline-details"><summary>查看协作过程记录 <span id="eventCount">0 条</span></summary><ol id="agentTimeline" class="timeline"><li>运行后显示后台真实执行事件。</li></ol></details>
    </section>
    <div class="results-heading"><div><span class="section-index">03</span><h2>决策结果</h2></div><button class="text-button" id="restoreLast" hidden>打开上次结果 ↗</button></div>
    <div class="result-grid">
      <section class="panel decision-panel"><div class="section-title"><div><span data-icon="target"></span><h2>关注建议</h2></div><span class="badge neutral" id="proposalBadge">等待评审</span></div><div id="fourResult" class="decision-content"></div><div id="ladeOverview" hidden></div><div id="taskList"></div></section>
      <section class="panel comparison-panel"><div class="section-title"><div><span data-icon="chart"></span><h2>策略回放对照</h2></div></div><p class="section-description">相同历史事件流下的规则效用，供事后比较。</p><div id="policyChart"></div><p class="chart-footnote">数值代表模拟关注行动的规则效用，不能换算成真实延误减少量。</p><div id="historicalFacts"></div></section>
    </div>
    <section class="panel evidence-strip"><div><span data-icon="shield"></span><div><h3>每个结果，都能回到原始数据</h3><p id="recordLabel">运行后可下载角色输入输出、原始SQL与逐轮回放记录。</p></div></div><div class="button-group"><button class="button secondary" id="ladeDownload" disabled>下载回放证据</button><button class="button secondary" id="fourDownload" disabled>下载协作证据</button><button class="text-button" data-page="evidence">查看证据与口径 ↗</button></div></section>
  </div>
  <div class="page" id="page-analysis" hidden><div class="page-heading"><div><div class="eyebrow">BUSINESS ANALYTICS</div><h1>经营分析<span class="title-dot">.</span></h1><p>查询Olist历史订单，按月份、类别与商家核对经营指标。</p></div><span class="badge neutral">Olist · 电商经营</span></div>ANALYSIS_SECTIONS<details class="panel archived"><summary>历史角色协作实验</summary><p class="muted">保留早期“数据、诊断、建议、审核”实验；当前原定四角色请使用协作工作台。</p>ARCHIVED_SECTION</details></div>
  <div class="page" id="page-lab" hidden><div class="page-heading"><div><div class="eyebrow">ALGORITHM LAB</div><h1>算法实验<span class="title-dot">.</span></h1><p>查看预测实验、预算求解与反馈重规划，区分历史事实和模拟结果。</p></div><span class="badge neutral">独立实验区</span></div>LAB_SECTIONS</div>
  <div class="page" id="page-evidence" hidden><div class="page-heading"><div><div class="eyebrow">EVIDENCE & TRACEABILITY</div><h1>证据与说明<span class="title-dot">.</span></h1><p>理解数据边界，复核本次运行的每一次交接与计算。</p></div></div>
    <div class="explain-grid"><article class="panel"><span class="explain-icon" data-icon="database"></span><h2>两份真实历史数据</h2><p>LaDe用于揽收任务与事件回放；Olist用于经营指标。两者独立建库，没有共同订单映射。</p></article><article class="panel"><span class="explain-icon" data-icon="network"></span><h2>四角色，分工协作</h2><p>真实模型模式使用同一模型的4次角色调用。工具演练不调用模型。后续回放轮次由确定性算法执行。</p></article><article class="panel"><span class="explain-icon" data-icon="shield"></span><h2>计算有据可查</h2><p>原始SQL独立对账，任务可见性与整数预算复核。资源费用和关注行动为模拟，不能据此声称真实物流收益。</p></article></div>
    <section class="panel"><h2>本次协作证据</h2><p class="muted">在协作工作台运行后，这里会显示完整记录。也可输入已保存的运行编号重新读取。</p><div class="row"><input id="savedRunId" placeholder="输入32位运行编号" aria-label="已有运行编号" maxlength="32"><button id="loadSaved" class="button secondary">载入记录</button></div><p id="savedStatus" role="status" class="muted"></p><details><summary>角色输入输出、数据指纹与原始SQL</summary><pre id="fourEvidence">请先运行四Agent协作。</pre></details></section>
    <section class="panel"><h2>真实任务回放证据</h2><details><summary>全日指标与限制说明</summary><pre id="ladeMetrics"></pre><pre id="ladeLimits"></pre></details><details><summary>四种策略逐轮记录与SQL</summary><div id="ladeTable" class="table-scroll"></div><pre id="ladeEvidence">请先查询真实任务或运行协作。</pre></details></section>
    <section class="panel"><h2>如何阅读时间与效果</h2><p>LaDe数据未提供年份，页面保留原始月日。初始建议只使用当时已知状态；全日指标和回放成绩在策略确定之后计算。每轮最多一次模拟关注行动，不会改变历史真实完成时间。</p><p>当前优先级按截止时间规则计算，尚不是LaDe延误概率模型。本地指纹链用于一致性检查，没有外部签名。</p></section>
  </div>
  <footer class="app-footer"><span>智策 · 物流决策工作台</span><span>真实历史数据 / 模拟资源行动 / 可复核决策</span></footer>
  </main>
</div>
<script src="/app.js"></script><script src="/workspace.js"></script>
</body></html>
'''
html=html.replace('ANALYSIS_SECTIONS','\n'.join(legacy(i) for i in (0,1,2,4))).replace('ARCHIVED_SECTION',legacy(5)).replace('LAB_SECTIONS','\n'.join(legacy(i) for i in (3,6,7,8)))
(ROOT/'agent_demo/index.html').write_text(html,encoding='utf8')
js=(ROOT/'work/frontend-before-redesign/app.js').read_text(encoding='utf8').split('let ladeEvidence=')[0]
(ROOT/'agent_demo/app.js').write_text(js,encoding='utf8')
print('已组织协作、经营分析、算法实验、证据四个工作区；保留既有实验控件。')
