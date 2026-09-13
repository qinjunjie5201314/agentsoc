"""D1 可视化 Demo 页（``/demo/audit``）—— 审计日志查询与回放。

三块：
  1. **实时统计**：AuditLogger 队列/已写/丢弃（3 秒轮询 /v1/audit/status）
  2. **风险事件流**：风险事件表（可按 layer/severity/action 过滤）
  3. **会话回放**：点会话 → 展开该会话全链路 risk_events + audit_logs
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>AgentSoc · 审计日志</title>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<style>
  :root {
    --bg: #f6f8fb; --fg: #0f172a; --muted: #64748b;
    --card: #ffffff; --border: #e2e8f0;
    --brand: #2563eb; --ok: #059669; --warn: #d97706; --bad: #dc2626;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 28px 24px 80px; }
  header { display: flex; align-items: center; gap: 16px; margin-bottom: 20px; }
  .logo { width: 40px; height: 40px; border-radius: 10px; background: linear-gradient(135deg, #059669, #2563eb);
    display: grid; place-items: center; color: #fff; font-size: 18px; }
  h1 { margin: 0; font-size: 22px; letter-spacing: -.01em; }
  .tag { color: var(--muted); font-size: 13px; }
  .back { margin-left: auto; }
  .back a { color: var(--brand); text-decoration: none; }
  .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
  @media (max-width: 800px) { .cards { grid-template-columns: repeat(2, 1fr); } }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  .card .k { color: var(--muted); font-size: 12px; }
  .card .v { font-size: 22px; font-weight: 700; margin-top: 2px; }
  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; margin-bottom: 16px; }
  .panel h3 { margin: 0 0 12px; font-size: 14px; color: var(--muted); font-weight: 500; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 500; background: #f8fafc; }
  tr:hover td { background: #fafbfc; }
  .mono { font-family: ui-monospace, SFMono-Regular, "Cascadia Code", monospace; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .b-high { background: #fee2e2; color: #b91c1c; }
  .b-medium { background: #fef3c7; color: #92400e; }
  .b-low { background: #dcfce7; color: #065f46; }
  .b-block { background: #fee2e2; color: #b91c1c; }
  .b-allow { background: #dcfce7; color: #065f46; }
  .b-confirm { background: #fef3c7; color: #92400e; }
  .b-L1 { background: #ede9fe; color: #5b21b6; }
  .b-L2 { background: #e0f2fe; color: #0369a1; }
  .b-L3 { background: #fce7f3; color: #be185d; }
  .b-L4 { background: #ffedd5; color: #9a3412; }
  .filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
  select, button { padding: 6px 10px; border-radius: 6px; border: 1px solid var(--border); background: #fff; font: inherit; font-size: 13px; }
  button.primary { background: var(--brand); color: #fff; border-color: var(--brand); cursor: pointer; }
  button.primary:hover { opacity: .9; }
  .empty { color: var(--muted); text-align: center; padding: 24px; font-size: 13px; }
  .replay { margin-top: 8px; padding: 12px; background: #f8fafc; border-radius: 8px; font-size: 12.5px; }
  .replay pre { margin: 4px 0 0; white-space: pre-wrap; word-break: break-all; color: #334155; }
  .toast { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%);
    background: #0f172a; color: #e2e8f0; padding: 10px 16px; border-radius: 8px; font-size: 13px;
    box-shadow: 0 8px 20px rgba(0,0,0,.18); opacity: 0; pointer-events: none; transition: opacity .25s; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">📋</div>
    <div>
      <h1>审计日志 <span class="tag">· D1</span></h1>
      <div class="tag">每次检测 / 工具调用落库，按会话回放完整攻击链路</div>
    </div>
    <div class="back"><a href="/dashboard">← 回到 Dashboard</a></div>
  </header>

  <div class="cards">
    <div class="card"><div class="k">落库状态</div><div class="v" id="stat-running">—</div></div>
    <div class="card"><div class="k">队列积压</div><div class="v" id="stat-queue">—</div></div>
    <div class="card"><div class="k">已写事件</div><div class="v" id="stat-written">—</div></div>
    <div class="card"><div class="k">丢弃（队列满）</div><div class="v" id="stat-dropped">—</div></div>
  </div>

  <div class="panel">
    <h3>风险事件</h3>
    <div class="filters">
      <select id="f-layer">
        <option value="">全部层级</option>
        <option>L1</option><option>L2</option><option>L3</option><option>L4</option>
      </select>
      <select id="f-severity">
        <option value="">全部风险</option>
        <option value="low">low</option><option value="medium">medium</option><option value="high">high</option>
      </select>
      <select id="f-action">
        <option value="">全部动作</option>
        <option value="allow">allow</option><option value="confirm">confirm</option><option value="block">block</option>
      </select>
      <button class="primary" id="btn-refresh">刷新</button>
    </div>
    <div id="events-table"><div class="empty">加载中…</div></div>
  </div>

  <div class="panel">
    <h3>会话回放</h3>
    <div class="filters"><button class="primary" id="btn-sessions">加载会话列表</button></div>
    <div id="sessions-table"><div class="empty">点击上方按钮加载</div></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  let toastTimer = null;
  function toast(t, ms=2000) { const e = $('#toast'); e.textContent = t; e.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => e.classList.remove('show'), ms); }

  function badge(text, cls) { return `<span class="badge ${cls}">${esc(text)}</span>`; }
  function layerBadge(l) { const m = {'L1':'b-L1','L2':'b-L2','L3':'b-L3','L4':'b-L4'}; return badge(l, m[l] || 'b-L3'); }
  function sevBadge(s) { const m = {'high':'b-high','medium':'b-medium','low':'b-low'}; return badge(s, m[s] || 'b-low'); }
  function actBadge(a) { const m = {'block':'b-block','allow':'b-allow','confirm':'b-confirm'}; return badge(a, m[a] || 'b-allow'); }

  // ---- 统计 ----
  async function pullStatus() {
    try {
      const r = await fetch('/v1/audit/status', {cache:'no-store'});
      const d = await r.json();
      $('#stat-running').textContent = d.running ? '运行中' : '已停';
      $('#stat-running').style.color = d.running ? 'var(--ok)' : 'var(--bad)';
      $('#stat-queue').textContent = d.queue_size ?? '—';
      $('#stat-written').textContent = d.written ?? '—';
      $('#stat-dropped').textContent = d.dropped ?? '—';
    } catch {}
  }

  // ---- 风险事件 ----
  async function loadEvents() {
    const params = new URLSearchParams();
    const layer = $('#f-layer').value, sev = $('#f-severity').value, act = $('#f-action').value;
    if (layer) params.set('layer', layer);
    if (sev) params.set('severity', sev);
    if (act) params.set('action', act);
    params.set('limit', '100');
    const r = await fetch('/v1/audit/events?' + params.toString());
    const d = await r.json();
    const box = $('#events-table');
    if (!d.events || !d.events.length) { box.innerHTML = '<div class="empty">暂无风险事件</div>'; return; }
    box.innerHTML = '<table><thead><tr><th>层级</th><th>风险</th><th>动作</th><th>规则</th><th>命中片段</th><th>会话</th><th>时间</th></tr></thead><tbody>' +
      d.events.map(e => `<tr>
        <td>${layerBadge(e.layer)}</td>
        <td>${sevBadge(e.risk_level)}</td>
        <td>${actBadge(e.action)}</td>
        <td class="mono">${esc(e.rule_id || '—')}</td>
        <td class="mono" style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((e.input_snippet||'').slice(0,60))}</td>
        <td class="mono">${esc((e.session_id||'').slice(0,16))}</td>
        <td class="mono">${esc((e.created_at||'').slice(11,19))}</td>
      </tr>`).join('') + '</tbody></table>';
  }

  // ---- 会话回放 ----
  async function loadSessions() {
    const r = await fetch('/v1/audit/sessions?limit=50');
    const d = await r.json();
    const box = $('#sessions-table');
    if (!d.sessions || !d.sessions.length) { box.innerHTML = '<div class="empty">暂无会话（先去 /demo 触发一次检测）</div>'; return; }
    box.innerHTML = '<table><thead><tr><th>会话 ID</th><th>用户</th><th>Agent</th><th>开始时间</th><th>操作</th></tr></thead><tbody>' +
      d.sessions.map(s => `<tr>
        <td class="mono">${esc(s.id)}</td>
        <td>${esc(s.user_id)}</td>
        <td>${esc(s.agent_id)}</td>
        <td class="mono">${esc((s.started_at||'').slice(0,19))}</td>
        <td><button onclick="replay('${esc(s.id)}')">回放</button></td>
      </tr>`).join('') + '</tbody></table><div id="replay-box"></div>';
  }

  async function replay(sid) {
    const r = await fetch('/v1/audit/sessions/' + encodeURIComponent(sid));
    const d = await r.json();
    const box = document.getElementById('replay-box') || document.createElement('div');
    box.id = 'replay-box';
    box.className = 'replay';
    let html = `<strong>会话 ${esc(sid)} 全链路</strong>`;
    html += `<div style="margin-top:6px">风险事件 ${d.risk_events.length} 条 · 审计日志 ${d.audit_logs.length} 条</div>`;
    if (d.risk_events.length) {
      html += '<pre>' + esc(JSON.stringify(d.risk_events.map(e => ({
        layer: e.layer, rule: e.rule_id, level: e.risk_level, action: e.action, snippet: e.input_snippet
      })), null, 2)) + '</pre>';
    }
    if (d.audit_logs.length) {
      html += '<div style="margin-top:6px">事件流：</div>';
      html += d.audit_logs.map(l => `<div class="mono" style="margin:2px 0">· ${esc(l.event_type)} ${esc((l.created_at||'').slice(11,19))}</div>`).join('');
    }
    box.innerHTML = html;
    if (!document.getElementById('replay-box')) { document.getElementById('sessions-table').appendChild(box); }
  }

  $('#btn-refresh').onclick = () => { loadEvents(); toast('已刷新'); };
  $('#btn-sessions').onclick = () => loadSessions();
  $('#f-layer').onchange = loadEvents;
  $('#f-severity').onchange = loadEvents;
  $('#f-action').onchange = loadEvents;

  // 初始化
  pullStatus(); loadEvents();
  setInterval(pullStatus, 3000);
  setInterval(loadEvents, 6000);
</script>
</body>
</html>
"""


def create_audit_demo_router() -> APIRouter:
    router = APIRouter(tags=["demo"])
    router.add_api_route(
        "/demo/audit",
        lambda: HTML,
        methods=["GET"],
        response_class=HTMLResponse,
        summary="D1 审计日志查询与回放",
    )
    return router


__all__ = ["create_audit_demo_router"]
