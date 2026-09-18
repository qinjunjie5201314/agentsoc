"""批量改名：AgentSentry -> AgentSoc。

规则：
1. 保留"火山引擎 AgentSentry"（指向火山引擎官方产品）。
2. 仅改 ASCII 文本文件，跳过二进制/__pycache__/.git。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
PROTECTED = "火山引擎 AgentSentry"  # 火山引擎产品名（在保护名单内，不替换）
PLACEHOLDER = "__VEC_AGENTSENTRY_PLACEHOLDER__"  # 临时占位，避免循环碰撞
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
TEXT_EXTS = {".py", ".md", ".toml", ".yaml", ".yml", ".ini", ".txt", ".cfg", ".html", ".json", ".Makefile"}
# 兼容无扩展名的 Makefile 文件
ALLOW_NAMES = {"Makefile", "Dockerfile", ".env", ".env.example"}

old_total = 0
new_total = 0
files_touched: list[str] = []


def patch(p: Path) -> None:
    global old_total, new_total
    try:
        text = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return
    if "AgentSentry" not in text:
        return
    before = text.count("AgentSentry")
    text2 = text.replace(PROTECTED, PLACEHOLDER)
    text3 = text2.replace("AgentSentry", "AgentSoc")
    text4 = text3.replace(PLACEHOLDER, PROTECTED)
    after = text4.count("AgentSentry")  # 仅剩下的"火山引擎 AgentSentry"
    p.write_text(text4, encoding="utf-8")
    old_total += before
    new_total += after
    files_touched.append(str(p.relative_to(ROOT)))


for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for f in filenames:
        full = Path(dirpath) / f
        ext = os.path.splitext(f)[1].lower()
        if ext not in TEXT_EXTS and f not in ALLOW_NAMES:
            continue
        patch(full)

print(f"=== 完成 ===")
print(f"修改的文件数: {len(files_touched)}")
print(f"原始 'AgentSentry' 出现次数: {old_total}")
print(f"替换后剩余 'AgentSentry' 次数（应为 2：仅火山引擎产品）: {new_total}")
for p in files_touched[:50]:
    print(f"  - {p}")
