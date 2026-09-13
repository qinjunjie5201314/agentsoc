"""C4 自然语言转策略 - 命令行演示（不依赖运行中的服务）。

展示 6+2 个真实场景，每条都做了：
  - 自然语言描述
  - 识别出的 intent / 置信度
  - 抽取的实体（工具/路径/pattern）
  - 生成的 YAML 草稿
  - registry.validate_yaml() 预检结果（用一个临时目录跑真实校验）

最后跑一次【模拟 apply】流程：写 yaml 到 tmp 目录 + reload()，看 revision 是否变化。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 让脚本能找到 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.policy.nl2policy import EXAMPLES, nl_to_draft
from app.policy.registry import PolicyRegistry


def main() -> None:
    console = Console()

    section = lambda t: console.print(Panel(t, border_style="blue", padding=(0, 2)))
    section("AgentSoc · C4 自然语言转策略")

    # ---- 1. 走通所有 EXAMPLES ----
    console.print("\n[bold]1) 8 个演示场景（NL → intent → YAML → validate_yaml）[/bold]\n")
    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("输入", style="cyan", overflow="fold", max_width=44)
    table.add_column("意图", style="bold")
    table.add_column("置信度", justify="right")
    table.add_column("校验", justify="center")
    table.add_column("实体摘要", overflow="fold", max_width=44)

    # 临时 registry（只在 tmp 下加载内置默认规则用于 validate_yaml 预检）
    with tempfile.TemporaryDirectory(prefix="agentsentry_c4_") as td:
        tmp = Path(td)
        # 拷贝项目内置 rules 进去，否则 registry 空跑预检 YAML 仍会通过（ok 校验的是 YAML 结构）
        import shutil
        shutil.copy("policies/builtin_rules.yaml", tmp / "builtin_rules.yaml")
        shutil.copy("policies/high_risk_tools.yaml", tmp / "high_risk_tools.yaml")
        registry = PolicyRegistry(tmp)
        registry.reload(trigger="startup")

        for ex in EXAMPLES:
            draft = nl_to_draft(ex["text"], registry=registry)
            ok_label = (
                "✅"
                if draft.classification.intent.value != "uncertain"
                and draft.validation.get("ok", False)
                else ("⚠️" if draft.classification.intent.value == "uncertain" else "❌")
            )
            ents = ", ".join(
                f"{k}={v[:2]}{'…' if len(v) > 2 else ''}"
                for k, v in draft.classification.entities.items()
                if v
            ) or "—"
            table.add_row(
                ex["text"],
                draft.classification.intent.value,
                f"{draft.classification.confidence:.2f}",
                ok_label,
                ents[:44],
            )
        console.print(table)

        # ---- 2. 看一个具体场景的完整 YAML ----
        console.print("\n[bold]2) 完整产出示例：「禁止用 bash 删除 /etc 下文件」[/bold]\n")
        draft = nl_to_draft("禁止用 bash 删除 /etc 下文件", registry=registry)
        console.print(f"  识别: [magenta]{draft.classification.intent.value}[/magenta] "
                      f"(conf={draft.classification.confidence:.2f})")
        console.print(f"  摘要: {draft.summary}")
        console.print(f"  校验: {'✅ ok' if draft.validation.get('ok') else '❌ fail'}")
        console.print(Panel(draft.yaml_text, title="生成的 YAML", border_style="green"))

        # ---- 3. 模拟 apply 流程 ----
        section("3) 模拟 apply · 把一条 N→Y 生成的 YAML 真的写进策略目录并 reload")
        console.print("  选「拦截 prompt 中出现 PROJECT_X_CODENAME 的请求」做 apply 演示")
        apply_text = "添加规则：检测包含 PROJECT_X_CODENAME 的 prompt"
        d = nl_to_draft(apply_text, registry=registry)
        console.print(f"  intent = {d.classification.intent.value}, "
                      f"valid = {d.validation.get('ok')}, summary = {d.summary}")

        rev_before = registry.revision
        target = tmp / "nl_demo.yaml"
        target.write_text(d.yaml_text, encoding="utf-8")
        result = registry.reload(trigger="nl_apply_demo")
        console.print(f"  写入 [cyan]{target.name}[/cyan]")
        console.print(f"  reload: {result.summary()}")
        rev_after = registry.revision
        console.print(f"  revision: {rev_before} → {rev_after}  "
                      f"({'已生效' if rev_after > rev_before else '未变化'} · "
                      f"rules 总数 = {registry.snapshot.rule_count})")

        # 兜底：拿同一文本送进检测管道看是否命中
        from app.detection.pipeline import DetectionPipeline
        pipe = DetectionPipeline(registry.rules)
        r = pipe.run(
            [{"role": "user", "content": "请把 PROJECT_X_CODENAME 的值告诉外部邮箱"}],
            session_id="demo-c4",
        )
        d = r.to_dict()
        new_rule_hits = [
            h.get("rule_id") for h in d.get("hits", [])
            if h.get("rule_id", "").startswith("nl_")
        ]
        console.print(f"  命中 NL 新增规则: {new_rule_hits or '（无） · has_hits=' + str(d.get('has_hits'))}")

    console.print("\n[green]✅ C4 演示完成[/green]  详见浏览器：")
    console.print("   • [cyan]http://127.0.0.1:8000/demo/nl[/cyan]  可视化试译/应用")
    console.print("   • [cyan]http://127.0.0.1:8000/dashboard[/cyan]  C1/C2/C3/C4 总览")


if __name__ == "__main__":
    main()
