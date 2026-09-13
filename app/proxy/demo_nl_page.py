"""C4 可视化 Demo 页（``/demo/nl``）—— 自然语言转策略。

三步走：
  1. 用户在文本框里写一句自然语言（自带 8 个预设场景可点）
  2. 点【试译】 → POST /v1/policy/nl/preview → 左侧展示 意图/置信度/实体
     右侧展示生成的 YAML 草稿 + registry validate_yaml 校验结果
  3. 点【应用】 → POST /v1/policy/nl/apply → 真正写入策略目录 + 触发 reload，
     页面会重新拉取 /v1/policy/version 看新策略是否生效（≈ 40ms）
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.policy.nl2policy import EXAMPLES

HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>AgentSoc · NL → 策略</title>
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
  .wrap { max-width: 1280px; margin: 0 auto; padding: 28px 24px 80px; }
  header { display: flex; align-items: center; gap: 16px; margin-bottom: 16px; }
  .logo { width: 40px; height: 40px; border-radius: 10px; background: linear-gradient(135deg, #2563eb, #7c3aed);
    display: grid; place-items: center; color: #fff; font-size: 18px; }
  h1 { margin: 0; font-size: 22px; letter-spacing: -.01em; }
  .tag { color: var(--muted); font-size: 13px; }
  .back { margin-left: auto; }
  .back a { color: var(--brand); text-decoration: none; }
  .bar { background: var(--card); border: 1px solid var(--border); border-radius: 14px;
    padding: 18px 20px; box-shadow: 0 1px 3px rgba(0,0,0,.04); margin-bottom: 18px; }
  textarea {
    width: 100%; min-height: 76px; padding: 12px 14px; font: inherit; font-size: 14px;
    border: 1px solid var(--border); border-radius: 8px; background: #fff; resize: vertical;
    outline: none;
  }
  textarea:focus { border-color: var(--brand); box-shadow: 0 0 0 3px rgba(37,99,235,.12); }
  .row { display: flex; gap: 10px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
  .btn {
    padding: 9px 16px; border-radius: 8px; border: 0; cursor: pointer; font: inherit; font-size: 14px; font-weight: 500;
    background: var(--brand); color: #fff; transition: opacity .15s;
  }
  .btn.gray { background: #f1f5f9; color: var(--fg); }
  .btn.ok   { background: var(--ok); }
  .btn[disabled] { opacity: .55; cursor: not-allowed; }
  .presets { margin-top: 12px; display: flex; gap: 6px; flex-wrap: wrap; }
  .preset-btn {
    font-size: 12.5px; padding: 6px 10px; border-radius: 999px;
    background: #eef2ff; color: var(--brand); border: 1px solid #c7d2fe; cursor: pointer;
  }
  .preset-btn:hover { background: #dbeafe; }
  .grid { display: grid; gap: 16px; grid-template-columns: 360px 1fr; }
  @media (max-width: 920px) { .grid { grid-template-columns: 1fr; } }
  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; }
  .panel h3 { margin: 0 0 10px; font-size: 14px; color: var(--muted); font-weight: 500; letter-spacing: .04em; text-transform: uppercase; }
  .kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 10px; font-size: 13px; }
  .kv k { color: var(--muted); }
  .kv v { font-family: ui-monospace, SFMono-Regular, "Cascadia Code", monospace; }
  .intent-tag {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 10px; border-radius: 999px; font-size: 12.5px; font-weight: 600;
  }
  .i-block_tool       { background: #fee2e2; color: var(--bad); }
  .i-block_tool_arg   { background: #ffedd5; color: var(--warn); }
  .i-add_rule         { background: #dcfce7; color: var(--ok); }
  .i-uncertain        { background: #f1f5f9; color: var(--muted); }
  .conf-bar { height: 4px; background: #f1f5f9; border-radius: 2px; margin-top: 4px; }
  .conf-bar > span { display: block; height: 100%; border-radius: 2px; background: var(--brand); }
  pre.code {
    margin: 0; padding: 12px 14px; background: #0f172a; color: #e2e8f0; border-radius: 8px;
    font: 12.5px/1.55 ui-monospace, SFMono-Regular, monospace; overflow: auto; max-height: 360px;
    white-space: pre-wrap; word-break: break-all;
  }
  .entities { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
  .entity {
    padding: 3px 8px; border-radius: 6px; background: #f1f5f9; font-size: 12px;
    font-family: ui-monospace, SFMono-Regular, monospace; color: #334155;
  }
  .entity.tool   { background: #dbeafe; color: #1e40af; }
  .entity.path   { background: #fef3c7; color: #92400e; }
  .entity.danger { background: #fee2e2; color: #b91c1c; }
  .validation {
    margin-top: 12px; padding: 10px 12px; border-radius: 8px;
    font-size: 13px;
  }
  .validation.ok { background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }
  .validation.bad { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
  .toast {
    position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%);
    background: #0f172a; color: #e2e8f0; padding: 10px 16px; border-radius: 8px; font-size: 13px;
    box-shadow: 0 8px 20px rgba(0,0,0,.18); opacity: 0; pointer-events: none; transition: opacity .25s;
  }
  .toast.show { opacity: 1; }
  .flash { background: #dcfce7 !important; transition: background .4s; }
  footer { margin-top: 24px; color: var(--muted); font-size: 12.5px; text-align: center; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">🪄</div>
    <div>
      <h1>自然语言转策略 <span class="tag">· C4 实验性</span></h1>
      <div class="tag">把"禁止 bash 删 /etc"这类口语化需求 → 自动写成可热加载的 YAML 片段</div>
    </div>
    <div class="back"><a href="/dashboard">← 回到 Dashboard</a></div>
  </header>

  <div class="bar">
    <textarea id="nl-input" placeholder="例：禁止 bash 删除 /etc 下的文件"></textarea>
    <div class="row">
      <label class="tag">严重度：</label>
      <select id="severity" style="padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:#fff;">
        <option value="low">low</option>
        <option value="medium">medium</option>
        <option value="high" selected>high</option>
        <option value="critical">critical</option>
      </select>
      <button class="btn" id="btn-preview">试译预览</button>
      <button class="btn ok" id="btn-apply">应用策略</button>
      <button class="btn gray" id="btn-clear">清空</button>
      <span class="tag" style="margin-left:auto;">策略版本：<b id="live-version">r- / -</b></span>
    </div>
    <div class="presets" id="presets"></div>
  </div>

  <div class="grid">
    <div class="panel">
      <h3>① 识别结果</h3>
      <div class="kv">
        <k>意图</k><v><span id="intent" class="intent-tag i-uncertain">—</span></v>
        <k>置信度</k>
        <v>
          <span id="confidence">—</span>
          <div class="conf-bar"><span id="conf-bar-span" style="width:0%"></span></div>
        </v>
        <k>判断依据</k><v id="reason" style="font-family:inherit;color:var(--muted);">—</v>
      </div>
      <h3 style="margin-top:18px">② 抽出的实体</h3>
      <div class="entities" id="entities">
        <span class="tag">无</span>
      </div>
      <div id="validation-box" class="validation ok" style="display:none"></div>
    </div>

    <div class="panel">
      <h3>③ 生成的 YAML 草稿</h3>
      <pre class="code" id="yaml">— 点【试译预览】开始 —</pre>
      <h3 style="margin-top:14px">④ 草稿概述</h3>
      <p id="summary" class="tag" style="margin:0">—</p>
    </div>
  </div>

  <footer>C4 是实验性能力：意图识别 + 模板生成 + registry.validate_yaml 预检三段式，不确定场景会标 <code>uncertain</code> 并把草稿留空。</footer>
</div>

<div class="toast" id="toast"></div>

<script>
  const $ = (s) => document.querySelector(s);
  const EXAMPLES = __EXAMPLES_JSON__;
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  let lastDraft = null;
  let toastTimer = null;
  function toast(text, ms=2200) {
    const t = $('#toast');
    t.textContent = text;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), ms);
  }
  function intentClass(intent) {
    if (intent === 'block_tool') return 'i-block_tool';
    if (intent === 'block_tool_arg_pattern') return 'i-block_tool_arg';
    if (intent === 'add_rule') return 'i-add_rule';
    return 'i-uncertain';
  }

  // ---- 预设 ----
  const ps = $('#presets');
  EXAMPLES.forEach(ex => {
    const b = document.createElement('button');
    b.className = 'preset-btn';
    b.textContent = `${ex.hint}`;
    b.title = ex.text;
    b.onclick = () => { $('#nl-input').value = ex.text; $('#btn-preview').click(); };
    ps.appendChild(b);
  });

  // ---- 试译预览 ----
  $('#btn-preview').onclick = async () => {
    const text = $('#nl-input').value.trim();
    if (!text) { toast('请输入自然语言'); return; }
    const sev = $('#severity').value;
    $('#btn-preview').disabled = true;
    try {
      const r = await fetch('/v1/policy/nl/preview', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text, severity: sev}),
      });
      const data = await r.json();
      if (!r.ok) {
        $('#yaml').textContent = data.detail || JSON.stringify(data, null, 2);
        $('#validation-box').className = 'validation bad';
        $('#validation-box').style.display = 'block';
        $('#validation-box').textContent = '❌ ' + (data.detail || '请求失败');
        return;
      }
      const draft = data.draft;
      lastDraft = draft;
      const intent = draft.classification.intent;
      const conf = draft.classification.confidence;
      $('#intent').textContent = intent;
      $('#intent').className = 'intent-tag ' + intentClass(intent);
      $('#confidence').textContent = conf.toFixed(2);
      $('#conf-bar-span').style.width = (conf * 100).toFixed(0) + '%';
      $('#reason').textContent = draft.classification.reason;

      // entities
      const entBox = $('#entities');
      entBox.innerHTML = '';
      const e = draft.classification.entities || {};
      const render = (k, cls) => (e[k] || []).map(v => {
        const s = document.createElement('span');
        s.className = 'entity ' + cls;
        s.textContent = `${k}: ${v}`;
        return s;
      });
      [...render('tools','tool'), ...render('paths','path'),
       ...render('quoted',''), ...render('dangerous_cmd','danger'),
       ...render('patterns','')].forEach(n => entBox.appendChild(n));
      if (!entBox.children.length) entBox.innerHTML = '<span class="tag">（无）</span>';

      // yaml
      $('#yaml').textContent = draft.yaml || '— 未生成（NL 不可解析） —';

      // summary
      $('#summary').textContent = draft.summary || '—';

      // validation
      const v = draft.validation || {};
      const ok = !!v.ok;
      const vbox = $('#validation-box');
      vbox.className = 'validation ' + (ok ? 'ok' : 'bad');
      vbox.style.display = 'block';
      if (ok) {
        vbox.textContent = '✅ registry.validate_yaml 通过：roles=' +
          (v.roles || []).join(', ') + ' · 警告 ' + (v.warnings || []).length + ' 条';
      } else {
        const errs = (v.errors || []).join('；') || '未知错误';
        vbox.textContent = '❌ 校验失败：' + errs;
      }
    } catch (e) {
      toast('请求失败：' + e.message);
    } finally {
      $('#btn-preview').disabled = false;
    }
  };

  // ---- 应用 ----
  $('#btn-apply').onclick = async () => {
    if (!lastDraft) { toast('请先点【试译预览】'); return; }
    if (!lastDraft.validation || !lastDraft.validation.ok) {
      toast('校验未通过，无法应用');
      return;
    }
    const sev = $('#severity').value;
    const text = $('#nl-input').value.trim();
    $('#btn-apply').disabled = true;
    try {
      const r = await fetch('/v1/policy/nl/apply', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text, severity: sev, force_reload: true}),
      });
      const data = await r.json();
      if (!r.ok) {
        toast('应用失败：' + (data.detail || 'HTTP ' + r.status));
        $('#validation-box').className = 'validation bad';
        $('#validation-box').style.display = 'block';
        $('#validation-box').textContent = '❌ ' + (data.detail || '应用失败');
        return;
      }
      const v = $('#live-version');
      v.classList.add('flash');
      setTimeout(() => v.classList.remove('flash'), 1400);
      toast(`✅ 已写入 ${data.filename} → revision ${data.reload.revision}`);
      await pullVersion();
    } catch (e) {
      toast('应用失败：' + e.message);
    } finally {
      $('#btn-apply').disabled = false;
    }
  };

  $('#btn-clear').onclick = () => {
    $('#nl-input').value = '';
    lastDraft = null;
    $('#yaml').textContent = '— 点【试译预览】开始 —';
    $('#summary').textContent = '—';
    $('#intent').textContent = '—';
    $('#intent').className = 'intent-tag i-uncertain';
    $('#confidence').textContent = '—';
    $('#conf-bar-span').style.width = '0%';
    $('#reason').textContent = '—';
    $('#entities').innerHTML = '<span class="tag">无</span>';
    $('#validation-box').style.display = 'none';
  };

  async function pullVersion() {
    try {
      const r = await fetch('/v1/policy/version', {cache:'no-store'});
      const v = await r.json();
      $('#live-version').textContent = `r${v.revision} / ${v.version}`;
    } catch {}
  }
  pullVersion();
  setInterval(pullVersion, 3000);
</script>
</body>
</html>
"""


def create_nl_demo_router() -> APIRouter:
    router = APIRouter(tags=["demo"])
    import json as _json
    rendered = HTML.replace("__EXAMPLES_JSON__", _json.dumps(EXAMPLES, ensure_ascii=False))
    router.add_api_route(
        "/demo/nl",
        lambda: rendered,
        methods=["GET"],
        response_class=HTMLResponse,
        summary="C4 自然语言转策略 - 可视化试译/应用",
    )
    return router


__all__ = ["create_nl_demo_router"]
