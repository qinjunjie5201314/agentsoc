"""Dashboard 入口 - 把三个演示页聚合成一个总览看板。

设计：light theme、卡片式、实时策略版本号、自链接一键跳转。
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse

HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>AgentSoc · Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<style>
  :root {
    --bg: #f6f8fb; --fg: #0f172a; --muted: #64748b;
    --card: #ffffff; --border: #e2e8f0;
    --brand: #2563eb; --brand-2: #7c3aed; --brand-3: #059669; --brand-4: #d97706; --brand-5: #0f766e;
    --warn: #d97706; --danger: #dc2626;
    --shadow: 0 1px 3px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.04);
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 48px 24px 80px; }
  header { display: flex; align-items: center; gap: 16px; margin-bottom: 36px; }
  .logo {
    width: 44px; height: 44px; border-radius: 12px;
    background: linear-gradient(135deg, var(--brand) 0%, var(--brand-2) 100%);
    display: grid; place-items: center; color: #fff; font-size: 22px;
  }
  h1 { margin: 0; font-size: 28px; letter-spacing: -.01em; }
  .tag { color: var(--muted); font-size: 14px; }
  .pills { display: flex; gap: 8px; margin-left: auto; flex-wrap: wrap; }
  .pill {
    padding: 6px 12px; border-radius: 999px; background: var(--card); border: 1px solid var(--border);
    font-size: 12px; color: var(--muted);
    display: inline-flex; align-items: center; gap: 6px;
  }
  .pill b { color: var(--fg); font-weight: 600; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e; box-shadow: 0 0 0 3px rgba(34,197,94,.12); }
  .dot.off { background: #94a3b8; box-shadow: 0 0 0 3px rgba(148,163,184,.12); }
  .grid { display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
  .card {
    background: var(--card); border: 1px solid var(--border); border-radius: 16px;
    box-shadow: var(--shadow); padding: 24px; display: flex; flex-direction: column; gap: 12px;
    transition: transform .15s, box-shadow .15s, border-color .15s;
  }
  .card:hover { transform: translateY(-2px); box-shadow: 0 4px 10px rgba(0,0,0,.06), 0 12px 32px rgba(0,0,0,.06); border-color: #c7d2fe; }
  .card h2 { margin: 0; font-size: 18px; display: flex; align-items: center; gap: 10px; }
  .badge { font-size: 11px; padding: 3px 8px; border-radius: 999px; color: #fff; font-weight: 600; }
  .b-c1 { background: var(--brand); } .b-c2 { background: var(--brand-2); } .b-c3 { background: var(--brand-3); } .b-c4 { background: var(--brand-4); } .b-d1 { background: var(--brand-5); }
  .desc { color: var(--muted); font-size: 14px; line-height: 1.6; min-height: 64px; }
  .meta { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip {
    padding: 4px 10px; border-radius: 6px; background: #f1f5f9; color: #334155;
    font-size: 12px; font-family: ui-monospace, SFMono-Regular, "Cascadia Code", monospace;
  }
  .actions { margin-top: auto; display: flex; gap: 8px; align-items: center; padding-top: 8px; }
  .btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 8px 14px; border-radius: 8px; background: var(--brand); color: #fff;
    text-decoration: none; font-size: 14px; font-weight: 500;
  }
  .btn.gray { background: #f1f5f9; color: var(--fg); }
  .btn:hover { opacity: .92; }
  footer {
    margin-top: 40px; padding-top: 20px; border-top: 1px dashed var(--border); color: var(--muted); font-size: 13px;
    display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px;
  }
  footer a { color: var(--brand); text-decoration: none; }
  .live { transition: background .4s; }
  .live.flash { background: #dcfce7; }
  code { font-family: ui-monospace, SFMono-Regular, "Cascadia Code", monospace; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">🛡️</div>
    <div>
      <h1>AgentSoc <span class="tag">· 演示看板</span></h1>
      <div class="tag">AI 助手安全防护 · L1-L4 纵深防御</div>
    </div>
    <div class="pills">
      <span class="pill"><span id="live-dot" class="dot"></span><span id="live-text">探活中…</span></span>
      <span class="pill">v<span id="app-ver">-</span></span>
      <span class="pill"><b id="policy-ver">-</b></span>
    </div>
  </header>

  <div class="grid">
    <div class="card">
      <h2><span class="badge b-c1">C1</span> prompt 注入拦截</h2>
      <div class="desc">
        演示 <b>L1 归一化 → L2 来源隔离 → L3 规则引擎+判别模型</b> 对一条攻击 prompt 的纵深防御。
        预设 12 个真实越权场景，实时渲染每一层匹配到的细节与融合后的最终动作。
      </div>
      <div class="meta"><span class="chip">L1 归一化</span><span class="chip">L2 来源隔离</span><span class="chip">L3 规则+判别</span></div>
      <div class="actions">
        <a class="btn" href="/demo">▶ 打开 →</a>
        <a class="btn gray" href="/v1/policy/rules">查看规则清单</a>
      </div>
    </div>

    <div class="card">
      <h2><span class="badge b-c2">C2</span> 工具调用拦截</h2>
      <div class="desc">
        模型决定要调工具时，<b>L4 输出兜底</b> 在执行前把它拦下，并回灌 <code>tool_result</code> 错误，
        让模型看到结果再说人话。Dry-Run 零副作用执行器，Anthropic / OpenAI 双协议嗅探。
      </div>
      <div class="meta"><span class="chip">L4 工具体检</span><span class="chip">三道关卡</span><span class="chip">Agent Loop</span></div>
      <div class="actions">
        <a class="btn" href="/demo/tools">▶ 打开 →</a>
        <a class="btn gray" href="/v1/policy/tools">查看工具策略</a>
      </div>
    </div>

    <div class="card">
      <h2><span class="badge b-c3">C3</span> 策略配置中心</h2>
      <div class="desc">
        YAML 改完 <b>毫秒级</b> 生效，无需重启。watchdog 事件驱动 ~37ms 延迟，内容寻址版本号避免噪声，
        解析失败 fail-safe 回滚。本页可实时看版本跳动 + 失败历史。
      </div>
      <div class="meta"><span class="chip">热更新</span><span class="chip">fail-safe</span><span class="chip">策略 API</span></div>
      <div class="actions">
        <a class="btn" href="/demo/policy">▶ 打开 →</a>
        <a class="btn gray" href="/v1/policy">策略总览</a>
      </div>
    </div>

    <div class="card">
      <h2><span class="badge b-c4">C4</span> 自然语言转策略</h2>
      <div class="desc">
        写一句人话「禁止 bash 删 /etc 下的文件」→ 自动翻译成可热加载的 YAML 草稿，
        经 <code>validate_yaml()</code> 预检后一键应用；不确定场景标 <b>uncertain</b> 并把草稿留空。
      </div>
      <div class="meta"><span class="chip">NL → YAML</span><span class="chip">预检</span><span class="chip">apply</span></div>
      <div class="actions">
        <a class="btn" href="/demo/nl">▶ 打开 →</a>
        <a class="btn gray" href="/v1/policy/nl/examples">查看示例</a>
      </div>
    </div>

    <div class="card">
      <h2><span class="badge b-d1">D1</span> 审计日志</h2>
      <div class="desc">
        每次检测 / 工具调用落库，带 <b>策略版本</b> 可溯源。按会话回放完整攻击链路：
        哪条被拦、命中哪条规则、用的是哪版策略。
      </div>
      <div class="meta"><span class="chip">异步落库</span><span class="chip">会话回放</span><span class="chip">策略溯源</span></div>
      <div class="actions">
        <a class="btn" href="/demo/audit">▶ 打开 →</a>
        <a class="btn gray" href="/v1/audit/events">事件 API</a>
      </div>
    </div>
  </div>

  <div class="grid" style="margin-top: 20px; grid-template-columns: repeat(auto-fit, minmax(220px,1fr));">
    <div class="card" style="padding:16px;">
      <div class="tag">交互入口</div>
      <div style="font-size:18px;"><a href="/docs" style="color:var(--brand);text-decoration:none;">📖 OpenAPI Docs</a></div>
    </div>
    <div class="card" style="padding:16px;">
      <div class="tag">健康</div>
      <div style="font-size:18px;"><a href="/health" style="color:var(--brand);text-decoration:none;">💓 /health</a></div>
    </div>
    <div class="card" style="padding:16px;">
      <div class="tag">REST</div>
      <div style="font-size:18px;"><a href="/v1/chat/completions" style="color:var(--brand);text-decoration:none;">💬 /v1/chat/completions</a></div>
    </div>
    <div class="card" style="padding:16px;">
      <div class="tag">REST</div>
      <div style="font-size:18px;"><a href="/v1/tools/guard" style="color:var(--brand);text-decoration:none;">🧰 /v1/tools/guard</a></div>
    </div>
  </div>

  <footer>
    <span>口径：内置 mock LLM 跑所有链路，<code>policies/*.yaml</code> 全在内存内热重载。</span>
    <span>↻ <a href="javascript:location.reload()">刷新本页</a></span>
  </footer>
</div>

<script>
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  async function pull() {
    try {
      const r = await fetch('/v1/info', { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      const info = await r.json();
      const dot = $('#live-dot');
      const text = $('#live-text');
      if (dot.classList.contains('off')) { dot.classList.remove('off'); text.textContent = '服务正常'; }
      $('#app-ver').textContent = info.version || '-';

      const pill = $('#policy-ver').parentElement;
      const newText = 'r' + info.policy_revision + ' / ' + info.policy_version;
      const cur = $('#policy-ver').textContent;
      if (cur !== '-' && cur !== newText) {
        pill.classList.add('flash');
        setTimeout(() => pill.classList.remove('flash'), 1400);
      }
      $('#policy-ver').textContent = newText;
    } catch (e) {
      $('#live-dot').classList.add('off');
      $('#live-text').textContent = '服务离线';
    }
  }
  pull();
  setInterval(pull, 3000);
</script>
</body>
</html>
"""


def create_dashboard_router() -> APIRouter:
    """Dashboard 入口路由。"""
    router = APIRouter(tags=["demo"])
    router.add_api_route(
        "/dashboard",
        lambda: HTMLResponse(HTML),
        methods=["GET"],
        response_class=HTMLResponse,
        summary="演示总览看板（C1/C2/C3 一键跳转 + 实时策略版本）",
    )
    return router


def mount_dashboard(app: FastAPI) -> None:
    app.include_router(create_dashboard_router())
