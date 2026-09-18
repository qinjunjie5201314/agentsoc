"""Fleet 看板页面 —— 终端总览 + 单终端详情。

两级视图：
  - ``/fleet``              总览：总数 / 在线 / 离线 + 终端列表（点击进详情）
  - ``/fleet/agent/{id}``   详情：单台终端的联通状态、统计与请求流水

页面为纯静态 HTML + fetch 轮询，数据来自 ``/v1/fleet/*`` 只读接口。
只监听内网，不对外暴露，与现有 demo 页同一策略。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

_SHARED_CSS = """
  :root{
    --bg:#f4f6fb; --card:#ffffff; --ink:#1f2733; --sub:#6b7686;
    --line:#e6eaf1; --brand:#2563eb; --ok:#16a34a; --ok-bg:#e8f7ec;
    --bad:#dc2626; --bad-bg:#fdecec; --warn:#d97706; --warn-bg:#fdf3e3;
    --shadow:0 1px 2px rgba(16,24,40,.04), 0 8px 24px rgba(16,24,40,.05);
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);
    font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
  .wrap{max-width:1180px;margin:0 auto;padding:28px 22px 56px}
  header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:22px}
  h1{margin:0;font-size:20px;font-weight:600;letter-spacing:-.01em}
  .sub{color:var(--sub);font-size:13px}
  .grow{flex:1}
  .stamp{color:var(--sub);font-size:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:18px}
  .kpi{padding:18px 20px;display:flex;flex-direction:column;gap:4px}
  .kpi .num{font-size:30px;font-weight:600;line-height:1.1;letter-spacing:-.02em}
  .kpi .lbl{color:var(--sub);font-size:12px}
  .kpi.ok .num{color:var(--ok)} .kpi.bad .num{color:var(--bad)}
  .kpi.hit .num{color:var(--warn)}
  .card-hd{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line);flex-wrap:wrap}
  .card-hd h2{margin:0;font-size:14px;font-weight:600}
  .hint{color:var(--sub);font-size:12px;margin-left:auto}
  input[type=search]{border:1px solid var(--line);border-radius:8px;padding:7px 11px;font-size:13px;outline:none;min-width:200px;background:#fbfcfe}
  input[type=search]:focus{border-color:#b9cdf7;background:#fff}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:11px 14px;border-bottom:1px solid var(--line);white-space:nowrap}
  th{color:var(--sub);font-weight:500;font-size:12px;background:#fafbfe}
  tbody tr{cursor:pointer;transition:background .12s}
  tbody tr:hover{background:#f7f9fe}
  tbody tr.row-off{background:#fffafa}
  tbody tr.row-off .host{color:var(--sub)}
  .host{color:var(--brand);font-weight:500}
  .mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--sub);font-size:12px}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
  .dot.on{background:var(--ok);box-shadow:0 0 0 3px rgba(22,163,74,.12)}
  .dot.off{background:#c3c9d4;box-shadow:0 0 0 3px rgba(195,201,212,.18)}
  .badge{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:500}
  .badge.on{background:var(--ok-bg);color:#15803d} .badge.off{background:var(--bad-bg);color:#b91c1c}
  .empty{padding:34px 20px;text-align:center;color:var(--sub);font-size:13px}
  .grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-bottom:16px}
  .grid4{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:16px}
  .field{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px dashed var(--line);font-size:13px}
  .field:last-child{border-bottom:none}
  .field .k{color:var(--sub)}
  .field .v{font-weight:500}
  .pad{padding:16px 20px}
  .pad-t{padding:16px 20px 4px}
  .big{display:flex;align-items:center;gap:12px;padding:18px 20px}
  .big .txt{display:flex;flex-direction:column}
  .big .txt b{font-size:17px;font-weight:600}
  .big .txt span{color:var(--sub);font-size:12px}
  .pill-lg{width:12px;height:12px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 5px rgba(22,163,74,.12)}
  .pill-lg.off{background:#c3c9d4;box-shadow:0 0 0 5px rgba(195,201,212,.18)}
  .banner{margin-bottom:16px;padding:12px 16px;border-radius:12px;background:var(--bad-bg);color:#b91c1c;font-size:13px;border:1px solid #f7d4d4}
  .banner.warn{background:var(--warn-bg);color:#b45309;border-color:#f6e0bd}
  .back{color:var(--brand);text-decoration:none;font-size:13px}
  .back:hover{text-decoration:underline}
  .tag{font-size:11px;padding:2px 7px;border-radius:6px;background:#f1f5f9;color:#475569;font-family:ui-monospace,Consolas,monospace}
  .tag.block{background:var(--bad-bg);color:#b91c1c} .tag.pass{background:var(--ok-bg);color:#15803d}
  .tag.error{background:var(--warn-bg);color:#b45309}
  footer{margin-top:26px;color:var(--sub);font-size:12px;text-align:center}
"""


def _nav() -> str:
    return """
  <header>
    <h1>AgentSoc · 终端总览</h1>
    <span class="sub" id="sub">桌面代理在线情况 · 终端主动上报</span>
    <span class="grow"></span>
    <span class="stamp" id="stamp">正在加载…</span>
  </header>
"""


_OVERVIEW_BODY = """
  <section class="kpis">
    <div class="card kpi"><div class="num" id="k-total">0</div><div class="lbl">终端总数</div></div>
    <div class="card kpi ok"><div class="num" id="k-online">0</div><div class="lbl">在线</div></div>
    <div class="card kpi bad"><div class="num" id="k-offline">0</div><div class="lbl">离线</div></div>
    <div class="card kpi hit"><div class="num" id="k-blocked">0</div><div class="lbl">累计命中规则拦截</div></div>
  </section>

  <section class="card">
    <div class="card-hd">
      <h2>终端列表</h2>
      <input type="search" id="q" placeholder="搜索主机名 / 别名 / IP" />
      <span class="hint">点击某行进入该终端详情看板</span>
    </div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr>
          <th>状态</th><th>主机名</th><th>别名</th><th>模式</th><th>版本</th>
          <th>网关延迟</th><th>总请求</th><th>命中拦截</th><th>最后上报</th><th>来源 IP</th>
        </tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div id="empty" class="empty" style="display:none">
      暂无终端上报。请确认终端已配置 upstream 网关并重启代理服务。
    </div>
  </section>

  <footer>看板仅内网可访问 · 数据为终端上报的内存快照，进程重启即清空</footer>
"""

_OVERVIEW_JS = """
<script>
(function(){
  var $ = function(id){ return document.getElementById(id); };
  var agents = [];
  var OFFLINE_AFTER = 180;

  function esc(s){
    return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c];
    });
  }
  function ago(sec){
    sec = Math.max(0, Math.round(sec || 0));
    if (sec < 60) return sec + ' 秒前';
    if (sec < 3600) return Math.floor(sec / 60) + ' 分钟前';
    if (sec < 86400) return Math.floor(sec / 3600) + ' 小时前';
    return Math.floor(sec / 86400) + ' 天前';
  }
  function uptime(sec){
    sec = Math.max(0, Math.round(sec || 0));
    if (sec < 3600) return Math.floor(sec / 60) + ' 分钟';
    if (sec < 86400) return Math.floor(sec / 3600) + ' 小时 ' + Math.floor((sec % 3600) / 60) + ' 分';
    return Math.floor(sec / 86400) + ' 天 ' + Math.floor((sec % 86400) / 3600) + ' 小时';
  }

  function render(){
    var q = ($('q').value || '').trim().toLowerCase();
    var list = agents.filter(function(a){
      if (!q) return true;
      return [a.hostname, a.agent_name, a.agent_id, a.source_ip].some(function(v){
        return String(v == null ? '' : v).toLowerCase().indexOf(q) >= 0;
      });
    });
    var html = list.map(function(a){
      var conn = a.connectivity || {};
      var st = a.stats || {};
      var lat = conn.ok ? (conn.latency_ms + ' ms') : '—';
      var dotCls = a.online ? 'on' : 'off';
      return '<tr class="' + (a.online ? '' : 'row-off') + '" data-id="' + esc(a.agent_id) + '">'
        + '<td><span class="dot ' + dotCls + '"></span>' + (a.online ? '在线' : '离线') + '</td>'
        + '<td class="host">' + esc(a.hostname) + '</td>'
        + '<td>' + esc(a.agent_name || '—') + '</td>'
        + '<td>' + esc(a.mode || '—') + '</td>'
        + '<td>' + esc(a.version || '—') + '</td>'
        + '<td>' + lat + '</td>'
        + '<td>' + (st.total || 0) + '</td>'
        + '<td style="color:' + ((st.blocked || 0) > 0 ? '#d97706' : 'inherit') + '">' + (st.blocked || 0) + '</td>'
        + '<td>' + ago(a.silent_sec) + '</td>'
        + '<td class="mono">' + esc(a.source_ip || '—') + '</td>'
        + '</tr>';
    }).join('');
    $('rows').innerHTML = html;
    $('empty').style.display = list.length ? 'none' : 'block';
    Array.prototype.forEach.call($('rows').querySelectorAll('tr'), function(tr){
      tr.addEventListener('click', function(){
        location.href = '/fleet/agent/' + encodeURIComponent(tr.getAttribute('data-id'));
      });
    });
  }

  function load(){
    fetch('/v1/fleet/agents', { cache: 'no-store' }).then(function(r){ return r.json(); }).then(function(d){
      agents = d.agents || [];
      var s = d.summary || {};
      var blocked = 0;
      agents.forEach(function(a){ blocked += (a.stats && a.stats.blocked) || 0; });
      $('k-total').textContent = s.total == null ? agents.length : s.total;
      $('k-online').textContent = s.online == null ? 0 : s.online;
      $('k-offline').textContent = s.offline == null ? 0 : s.offline;
      $('k-blocked').textContent = blocked;
      OFFLINE_AFTER = s.offline_after || OFFLINE_AFTER;
      $('sub').textContent = '桌面代理在线情况 · 每 ' + (s.report_interval || 60) + ' 秒上报 · 超过 '
        + Math.round(OFFLINE_AFTER / 60) + ' 分钟未上报判离线';
      $('stamp').textContent = '更新于 ' + new Date().toLocaleTimeString('zh-CN');
      render();
    }).catch(function(){
      $('stamp').textContent = '读取失败，重试中…';
    });
  }

  $('q').addEventListener('input', render);
  load();
  setInterval(load, 3000);
})();
</script>
"""


_DETAIL_BODY = """
  <div id="banner" class="banner" style="display:none"></div>

  <div class="grid2">
    <section class="card">
      <div class="card-hd"><h2>代理联通状态</h2></div>
      <div class="big">
        <span class="pill-lg" id="conn-dot"></span>
        <span class="txt">
          <b id="conn-text">读取中…</b>
          <span id="conn-sub">—</span>
        </span>
      </div>
      <div class="pad-t">
        <div class="field"><span class="k">上报网关</span><span class="v mono" id="f-upstream">—</span></div>
        <div class="field"><span class="k">最近上报</span><span class="v" id="f-last">—</span></div>
        <div class="field"><span class="k">上报次数</span><span class="v" id="f-count">—</span></div>
        <div class="field"><span class="k">说明</span><span class="v" id="f-err">—</span></div>
      </div>
    </section>

    <section class="card">
      <div class="card-hd"><h2>终端信息</h2></div>
      <div class="pad-t">
        <div class="field"><span class="k">主机名称</span><span class="v" id="f-host">—</span></div>
        <div class="field"><span class="k">终端别名</span><span class="v" id="f-name">—</span></div>
        <div class="field"><span class="k">来源 IP</span><span class="v mono" id="f-ip">—</span></div>
        <div class="field"><span class="k">运行模式</span><span class="v" id="f-mode">—</span></div>
        <div class="field"><span class="k">代理版本</span><span class="v" id="f-ver">—</span></div>
        <div class="field"><span class="k">已运行</span><span class="v" id="f-uptime">—</span></div>
      </div>
    </section>
  </div>

  <section class="grid4">
    <div class="card kpi"><div class="num" id="s-total">0</div><div class="lbl">总请求</div></div>
    <div class="card kpi hit"><div class="num" id="s-blocked">0</div><div class="lbl">命中规则拦截</div></div>
    <div class="card kpi ok"><div class="num" id="s-passed">0</div><div class="lbl">放行通过</div></div>
    <div class="card kpi bad"><div class="num" id="s-errs">0</div><div class="lbl">网关错误</div></div>
  </section>

  <section class="card">
    <div class="card-hd">
      <h2>命中规则 / 请求流水</h2>
      <span class="hint">该终端最近上报的检测记录（最多 20 条）</span>
    </div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr>
          <th>时间</th><th>动作</th><th>方法</th><th>路径</th><th>风险</th>
          <th>评分</th><th>命中规则</th><th>策略</th><th>说明</th>
        </tr></thead>
        <tbody id="flows"></tbody>
      </table>
    </div>
    <div id="flows-empty" class="empty" style="display:none">该终端暂无请求记录</div>
  </section>

  <footer>数据来自该终端最近一次心跳上报的快照</footer>
"""

_DETAIL_JS = """
<script>
(function(){
  var $ = function(id){ return document.getElementById(id); };
  var agentId = decodeURIComponent(location.pathname.split('/').pop() || '');

  function esc(s){
    return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c];
    });
  }
  function ago(sec){
    sec = Math.max(0, Math.round(sec || 0));
    if (sec < 60) return sec + ' 秒前';
    if (sec < 3600) return Math.floor(sec / 60) + ' 分钟前';
    if (sec < 86400) return Math.floor(sec / 3600) + ' 小时前';
    return Math.floor(sec / 86400) + ' 天前';
  }
  function uptime(sec){
    sec = Math.max(0, Math.round(sec || 0));
    if (sec < 3600) return Math.floor(sec / 60) + ' 分钟';
    if (sec < 86400) return Math.floor(sec / 3600) + ' 小时 ' + Math.floor((sec % 3600) / 60) + ' 分';
    return Math.floor(sec / 86400) + ' 天 ' + Math.floor((sec % 86400) / 3600) + ' 小时';
  }
  function ts(ms){
    if (!ms) return '—';
    var d = new Date(ms);
    return d.toLocaleDateString('zh-CN') + ' ' + d.toLocaleTimeString('zh-CN');
  }

  function paint(a){
    var conn = a.connectivity || {};
    var st = a.stats || {};
    document.title = 'AgentSoc · ' + (a.hostname || agentId);

    $('conn-dot').className = 'pill-lg' + (conn.ok ? '' : ' off');
    $('conn-text').textContent = conn.ok ? '已联通' : '未联通';
    $('conn-sub').textContent = conn.ok ? ('延迟 ' + conn.latency_ms + ' ms') : (conn.error || '无法访问上游网关');
    $('f-upstream').textContent = a.upstream || '—';
    $('f-last').textContent = ago(a.silent_sec);
    $('f-count').textContent = (a.report_count || 0) + ' 次';
    $('f-err').textContent = conn.error || '正常';
    $('f-host').textContent = a.hostname || a.agent_id;
    $('f-name').textContent = a.agent_name || '—';
    $('f-ip').textContent = a.source_ip || '—';
    $('f-mode').textContent = a.mode || '—';
    $('f-ver').textContent = a.version || '—';
    $('f-uptime').textContent = uptime(a.uptime_sec);
    $('s-total').textContent = st.total || 0;
    $('s-blocked').textContent = st.blocked || 0;
    $('s-passed').textContent = st.passed || 0;
    $('s-errs').textContent = st.errors || 0;

    var badge = '<span class="badge ' + (a.online ? 'on' : 'off') + '">'
      + (a.online ? '在线' : '离线') + '</span>';
    $('hdr-badge').innerHTML = badge;
    $('hdr-host').textContent = a.hostname || a.agent_id;

    var banner = $('banner');
    if (!a.online){
      banner.style.display = 'block';
      banner.textContent = '该终端已 ' + ago(a.silent_sec) + '停止上报（超过离线阈值），以下为最后一次上报的快照。';
    } else {
      banner.style.display = 'none';
    }

    var rows = (a.recent || []).map(function(e){
      var act = e.action || 'pass';
      var cls = act === 'block' ? 'block' : (act === 'error' ? 'error' : 'pass');
      var label = act === 'block' ? '拦截' : (act === 'error' ? '错误' : '放行');
      var rules = (e.rules && e.rules.length) ? e.rules.join(', ') : '—';
      return '<tr>'
        + '<td class="mono">' + ts(e.ts) + '</td>'
        + '<td><span class="tag ' + cls + '">' + label + '</span></td>'
        + '<td>' + esc(e.method) + '</td>'
        + '<td class="mono">' + esc(e.path) + '</td>'
        + '<td>' + esc(e.risk || '—') + '</td>'
        + '<td>' + (e.score ? Number(e.score).toFixed(2) : '—') + '</td>'
        + '<td class="mono">' + esc(rules) + '</td>'
        + '<td class="mono">' + esc(e.policy_ver || '—') + '</td>'
        + '<td>' + esc(e.message || '—') + '</td>'
        + '</tr>';
    }).join('');
    $('flows').innerHTML = rows;
    $('flows-empty').style.display = rows ? 'none' : 'block';
  }

  function load(){
    fetch('/v1/fleet/agents/' + encodeURIComponent(agentId), { cache: 'no-store' })
      .then(function(r){
        if (!r.ok) throw new Error('not found');
        return r.json();
      })
      .then(function(a){
        paint(a);
        $('stamp').textContent = '更新于 ' + new Date().toLocaleTimeString('zh-CN');
      })
      .catch(function(){
        $('stamp').textContent = '读取失败，重试中…';
      });
  }

  load();
  setInterval(load, 3000);
})();
</script>
"""


def _page(title: str, body: str, js: str, extra_head: str = "") -> str:
    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"<title>{title}</title>\n"
        f"<style>{_SHARED_CSS}</style>\n"
        f"{extra_head}</head>\n<body>\n<div class=\"wrap\">\n"
        f"{body}\n</div>\n{js}\n</body>\n</html>\n"
    )


def render_overview_page() -> str:
    """总览页 HTML。"""
    return _page(
        "AgentSoc · 终端总览",
        _nav() + _OVERVIEW_BODY,
        _OVERVIEW_JS,
    )


def render_detail_page() -> str:
    """终端详情页 HTML。"""
    header = """
  <header>
    <a class="back" href="/fleet">← 返回终端总览</a>
    <h1 id="hdr-host">终端详情</h1>
    <span id="hdr-badge"></span>
    <span class="grow"></span>
    <span class="stamp" id="stamp">正在加载…</span>
  </header>
"""
    return _page(
        "AgentSoc · 终端详情",
        header + _DETAIL_BODY,
        _DETAIL_JS,
    )


def create_fleet_page_router() -> APIRouter:
    """构造 fleet 看板页面路由（无鉴权，内网视图）。"""
    router = APIRouter(tags=["fleet"])

    @router.get("/fleet", response_class=HTMLResponse)
    async def fleet_overview() -> HTMLResponse:
        """终端总览看板。"""
        return HTMLResponse(render_overview_page())

    @router.get("/fleet/agent/{agent_id}", response_class=HTMLResponse)
    async def fleet_agent_detail(agent_id: str) -> HTMLResponse:
        """单台终端详情看板（数据由前端按 agent_id 拉取）。"""
        return HTMLResponse(render_detail_page())

    return router


__all__ = ["create_fleet_page_router", "render_overview_page", "render_detail_page"]
