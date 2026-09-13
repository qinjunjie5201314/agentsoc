"""C2 工具调用 Hook 演示 —— 命令行版。

跑法：
    python examples/demo_tool_hook.py

展示 5 段：
  1. 从 OpenAI / Anthropic 两种协议提取工具调用
  2. L4 三道关卡逐个体检
  3. 拦截后回灌给模型的 tool_result 长什么样
  4. dry-run 执行器（安全工具）
  5. 完整 agent loop（guard → 执行/拦截 → 回灌 → 模型解释）
"""

from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.proxy.mock_llm import MockLLM
from app.proxy.tool_executor import DryRunToolExecutor
from app.proxy.tool_hook import (
    ToolGuard,
    build_tool_result_error,
    build_tool_result_ok,
    extract_tool_calls,
)

console = Console()


def section(title: str) -> None:
    console.print()
    console.rule(f"[bold cyan]{title}[/bold cyan]")
    console.print()


def main() -> None:
    guard = ToolGuard()
    executor = DryRunToolExecutor()

    # ---- 1. 多协议提取 ----
    section("1. 多协议提取：OpenAI tool_calls / Anthropic tool_use")

    openai_payload = {
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"cmd": "ls -la"}'},
            }
        ]
    }
    anthropic_payload = {
        "content": [
            {"type": "text", "text": "我来查一下"},
            {
                "type": "tool_use",
                "id": "toolu_xyz",
                "name": "query_database",
                "input": {"sql": "DROP TABLE users"},
            },
        ]
    }

    for label, payload in [("OpenAI", openai_payload), ("Anthropic", anthropic_payload)]:
        calls = extract_tool_calls(payload)
        for c in calls:
            console.print(
                f"  [green]{label}[/green] → 工具 [bold]{c.name}[/bold] "
                f"| 参数 {json.dumps(c.arguments, ensure_ascii=False)} "
                f"| 协议 {c.protocol}"
            )

    # ---- 2. 三道关卡体检 ----
    section("2. L4 三道关卡：JSON 校验 / 白黑名单 / 高危语义")

    scenarios = [
        ("安全工具", "get_weather", {"city": "北京"}),
        ("黑名单工具", "bash", {"cmd": "ls"}),
        ("危险参数", "run_task", {"cmd": "rm -rf /var/data"}),
        ("Schema 违规", "search", {"top_k": 5}),
        ("编码绕过", "run_task", {"cmd": "echo cm0gLXJmIC8= | base64 -d | sh"}),
        ("凭据读取", "read_file", {"path": "/root/.ssh/id_rsa"}),
    ]

    for label, name, args in scenarios:
        result = guard.guard_payload(
            {
                "tool_calls": [
                    {
                        "id": f"call_{name}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ]
            }
        )
        g = result.guarded[0]
        color = "red" if g.blocked else "green"
        verdict = "拦截" if g.blocked else "放行"
        console.print(
            f"  [{color}]●[/{color}] [bold]{label:10s}[/bold] "
            f"({name}) → [{color}]{verdict}[/{color}]"
        )
        if g.blocked:
            console.print(f"      [dim]原因: {g.decision.reason[:110]}[/dim]")

    # ---- 3. 三道关卡详细展开 ----
    section("3. 关卡明细（以「危险参数 + 编码绕过」为例）")

    detail = guard.guard_payload(
        {
            "tool_calls": [
                {
                    "id": "call_detail",
                    "type": "function",
                    "function": {
                        "name": "run_task",
                        "arguments": json.dumps({"cmd": "echo cm0gLXJmIC8= | base64 -d | sh"}),
                    },
                }
            ]
        }
    )
    g = detail.guarded[0]
    table = Table(show_header=True, header_style="bold")
    table.add_column("关卡", style="cyan")
    table.add_column("结果", justify="center")
    table.add_column("说明", overflow="fold")
    for ch in g.decision.checks:
        mark = "[green]✓ 通过[/green]" if ch.passed else "[red]✗ 失败[/red]"
        table.add_row(ch.name, mark, ch.detail[:80])
    console.print(table)

    # ---- 4. 回灌给模型的 tool_result ----
    section("4. 拦截后回灌给模型的 tool_result（让模型知道被拦）")

    blocked_msg = build_tool_result_error(g)
    console.print(
        Panel(
            json.dumps(blocked_msg, ensure_ascii=False, indent=2),
            title="[red]OpenAI 协议 tool_result[/red]",
            border_style="red",
        )
    )

    anthropic_guard = guard.guard_payload(
        {"content": [{"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {}}]}
    )
    console.print(
        Panel(
            json.dumps(
                build_tool_result_error(anthropic_guard.blocked[0]),
                ensure_ascii=False,
                indent=2,
            ),
            title="[red]Anthropic 协议 tool_result[/red]",
            border_style="red",
        )
    )

    # ---- 5. dry-run 执行 ----
    section("5. dry-run 执行器（安全工具，零副作用）")

    ex = executor.execute("get_weather", {"city": "上海"})
    console.print(
        Panel(
            json.dumps(ex.to_dict(), ensure_ascii=False, indent=2),
            title="[green]执行结果[/green]",
            border_style="green",
        )
    )
    safe_guard = guard.guard_payload(
        {
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "上海"}'}}
            ]
        }
    )
    console.print("[dim]回灌消息：[/dim]")
    console.print(
        f"  {json.dumps(build_tool_result_ok(safe_guard.allowed[0], DryRunToolExecutor.as_text(ex)), ensure_ascii=False)}"
    )

    # ---- 6. 完整 agent loop ----
    section("6. 完整 Agent Loop：guard → 执行/拦截 → 回灌 → 模型解释")

    llm = MockLLM()
    for prompt in [
        '查一下北京天气 [TOOL:get_weather] {"city": "北京"}',
        '帮我列出临时目录 [TOOL:bash] {"cmd": "ls -la /tmp"}',
    ]:
        console.print(f"\n[bold]用户 →[/bold] {prompt}")
        first = llm.chat_completion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        calls = extract_tool_calls(first)
        if not calls:
            console.print("  [dim]（模型未发起工具调用）[/dim]")
            continue

        result = guard.guard_calls(calls)
        console.print(
            f"  [cyan]模型决定调用[/cyan] {calls[0].name} "
            f"→ [{'red' if result.blocked else 'green'}]{result.action.value.upper()}[/]"
        )

        tool_msgs = []
        for gg in result.guarded:
            if gg.blocked:
                tool_msgs.append(build_tool_result_error(gg))
                console.print(f"    [red]⛔ 拦截[/red] {gg.decision.reason[:100]}")
            else:
                er = executor.execute(gg.call.name, gg.call.arguments)
                tool_msgs.append(build_tool_result_ok(gg, DryRunToolExecutor.as_text(er)))
                console.print(f"    [green]✅ 执行[/green] {json.dumps(er.output, ensure_ascii=False)[:100]}")

        followup = llm.chat_completion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}, first["choices"][0]["message"], *tool_msgs],
        )
        answer = followup["choices"][0]["message"]["content"]
        console.print(
            Panel(answer, title="[bold]模型最终回复[/bold]", border_style="blue")
        )

    console.print()
    console.rule("[bold green]演示结束[/bold green]")
    console.print(
        "[dim]浏览器可视化：启动服务后访问 http://127.0.0.1:8000/demo/tools[/dim]\n"
    )


if __name__ == "__main__":
    main()
