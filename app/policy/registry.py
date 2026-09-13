"""C3 策略配置中心 —— 策略的**单一事实来源** + 版本化 + 热更新。

为什么需要它
------------
在 C3 之前，策略散落在两处、且在进程启动时被"冻住"：

  - ``DetectionPipeline.from_yaml("policies/builtin_rules.yaml")`` 在 import 时读一次
  - ``ToolGuard()`` 在构造时读一次 ``high_risk_tools.yaml``

结果是**改一条规则必须重启服务**。对安全产品来说这是致命的：遇到新型注入攻击，
运营同学希望"改一行 YAML、2 秒内全网生效"，而不是发版重启。

核心设计
--------
1. **单一事实来源**：``policies/`` 目录下所有 ``*.yaml`` 都被扫描，按顶层键自动归类：
     - 含 ``rules``            → L3 检测规则
     - 含 ``blocked_tools`` / ``allowlist`` / ``dangerous_patterns`` → L4 工具策略
     - 含 ``tool_schemas``     → 工具参数 JSON Schema
   新增一个 YAML 文件即被自动纳入，不需要改代码。

2. **版本化**：每次内容变化产生新 ``revision``（递增）+ ``version``（内容 sha256 前 12 位）。
   客户端可轮询 ``/v1/policy/version`` 做变更感知；每条检测结果也带策略版本，
   事后可回答"这条拦截是哪版策略做出的"。

3. **热更新**：``PolicyWatcher`` 轮询目录 → ``registry.reload()``；pipeline / guard
   每次请求都读 registry 的最新快照，因此**无需重启、无需重建对象**。

4. **失败不降级（fail-safe）**：任一 YAML 解析失败时**整体保留上一版生效策略**，
   只把错误记进 ``errors`` 并写进 reload 历史。避免"改错一行 YAML 打挂整个服务"。

线程安全
--------
``reload()`` 与读取属性都在 ``RLock`` 保护下；快照对象一经产出即不可变，
读侧不会看到"改了一半"的中间状态。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.detection.egress import ToolPolicy
from app.detection.schema import Rule

logger = logging.getLogger(__name__)

# 顶层键 → 策略角色
_RULE_KEYS = ("rules",)
_TOOL_POLICY_KEYS = ("blocked_tools", "allowlist", "dangerous_patterns")
_SCHEMA_KEYS = ("tool_schemas",)

ROLE_RULES = "rules"
ROLE_TOOL_POLICY = "tool_policy"
ROLE_TOOL_SCHEMAS = "tool_schemas"

# 规则反序列化时可能抛出的异常（缺字段 / 枚举值非法 / 类型不对 / patterns 不是列表）
RULE_PARSE_ERRORS = (KeyError, ValueError, TypeError, AttributeError, IndexError)


# ============ 工具函数 ============


def default_policy_dir() -> Path:
    """策略目录：优先用配置里的 ``policy_dir``，不存在则回退到项目根 ``policies/``。"""
    try:
        from app.config import settings

        configured = Path(settings.policy_dir)
        if configured.is_dir():
            return configured
    except (ImportError, AttributeError, TypeError) as exc:  # pragma: no cover - 配置异常时回退
        logger.debug("读取 settings.policy_dir 失败，回退到项目根 policies/: %s", exc)
    return Path(__file__).resolve().parents[2] / "policies"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rule_hash(rule: Rule) -> str:
    """规则的稳定指纹（用于 diff 出"哪几条规则被改了"）。"""
    payload = json.dumps(rule.to_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _iso(ts: float) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _one_line(text: object, limit: int = 280) -> str:
    """把多行错误信息压成单行（YAML 异常自带换行与缩进，直接展示很难看）。"""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _dedupe(seq: Sequence[str]) -> list[str]:
    """保序去重。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _dedupe_patterns(seq: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """按 name 去重，后出现者覆盖（后面的文件优先级更高）。"""
    by_name: dict[str, Mapping[str, Any]] = {}
    for pat in seq:
        by_name[str(pat.get("name"))] = pat
    return list(by_name.values())


# ============ 数据结构 ============


@dataclass(frozen=True)
class FileFingerprint:
    """单个策略文件的指纹（用于变更检测与可视化）。"""

    name: str
    path: str
    sha256: str
    size: int
    mtime: float
    roles: tuple[str, ...] = ()

    @property
    def short_hash(self) -> str:
        return self.sha256[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "sha256": self.sha256,
            "short_hash": self.short_hash,
            "size": self.size,
            "mtime": self.mtime,
            "mtime_iso": _iso(self.mtime),
            "roles": list(self.roles),
        }


@dataclass(frozen=True)
class PolicySnapshot:
    """某一时刻的**不可变**策略快照。"""

    revision: int = 0
    version: str = "empty"
    loaded_at: float = 0.0
    rules: tuple[Rule, ...] = ()
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    tool_schemas: Mapping[str, dict[str, Any]] = field(default_factory=dict)
    files: tuple[FileFingerprint, ...] = ()
    warnings: tuple[str, ...] = ()

    # ---- 便捷属性 ----
    @property
    def loaded(self) -> bool:
        return self.revision > 0

    @property
    def rule_count(self) -> int:
        return len(self.rules)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def loaded_at_iso(self) -> str:
        return _iso(self.loaded_at)

    def rule_ids(self) -> set[str]:
        return {r.id for r in self.rules}

    def to_dict(self, *, include_rules: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "revision": self.revision,
            "version": self.version,
            "loaded": self.loaded,
            "loaded_at": self.loaded_at,
            "loaded_at_iso": self.loaded_at_iso,
            "rule_count": self.rule_count,
            "file_count": self.file_count,
            "files": [f.to_dict() for f in self.files],
            "tool_policy": {
                "mode": "allowlist" if self.tool_policy.allowlist else "blocklist",
                "blocked_tools": list(self.tool_policy.blocked_tools),
                "allowlist": list(self.tool_policy.allowlist),
                "dangerous_patterns": [
                    {"name": n, "pattern": p, "message": m}
                    for n, p, m in self.tool_policy.dangerous_patterns
                ],
            },
            "tool_schemas": {
                "count": len(self.tool_schemas),
                "tools": sorted(self.tool_schemas),
            },
            "warnings": list(self.warnings),
        }
        if include_rules:
            d["rules"] = [r.to_dict() for r in self.rules]
        return d


@dataclass(frozen=True)
class ReloadResult:
    """一次 reload 的结果（成败都记录）。"""

    changed: bool
    revision: int
    version: str
    previous_version: str | None
    trigger: str  # startup | watcher | manual | api
    at: float
    elapsed_ms: float = 0.0
    files_total: int = 0
    added_files: tuple[str, ...] = ()
    removed_files: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    added_rules: tuple[str, ...] = ()
    removed_rules: tuple[str, ...] = ()
    changed_rules: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    degraded: bool = False  # True = 解析失败，已保留上一版

    @property
    def at_iso(self) -> str:
        return _iso(self.at)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.errors:
            return f"❌ reload 失败（保留旧版 r{self.revision}）: {self.errors[0]}"
        if not self.changed:
            return f"= 无变化（仍是 r{self.revision} / {self.version}）"
        if self.previous_version is None:
            return f"✅ 首次加载 r{self.revision} / {self.version} · {len(self.added_rules)} 条规则 · {self.files_total} 个文件"
        return (
            f"✅ 已生效 r{self.revision} / {self.version}"
            f" · 规则 +{len(self.added_rules)}/-{len(self.removed_rules)}/~{len(self.changed_rules)}"
            f" · 文件 +{len(self.added_files)}/-{len(self.removed_files)}/~{len(self.changed_files)}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "ok": self.ok,
            "degraded": self.degraded,
            "revision": self.revision,
            "version": self.version,
            "previous_version": self.previous_version,
            "trigger": self.trigger,
            "at": self.at,
            "at_iso": self.at_iso,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "files_total": self.files_total,
            "added_files": list(self.added_files),
            "removed_files": list(self.removed_files),
            "changed_files": list(self.changed_files),
            "added_rules": list(self.added_rules),
            "removed_rules": list(self.removed_rules),
            "changed_rules": list(self.changed_rules),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "summary": self.summary(),
        }


# ============ 注册中心 ============


class PolicyRegistry:
    """策略配置中心。

    用法::

        registry = PolicyRegistry("policies")
        registry.reload(trigger="startup")
        pipeline = DetectionPipeline(registry.rules, registry=registry)
        guard = ToolGuard(registry=registry)
        watcher = PolicyWatcher(registry).start()
    """

    def __init__(
        self,
        policy_dir: str | Path | None = None,
        *,
        max_history: int = 50,
        on_reload: Callable[[ReloadResult], None] | None = None,
    ) -> None:
        self.policy_dir = Path(policy_dir) if policy_dir is not None else default_policy_dir()
        self.max_history = max_history

        self._lock = threading.RLock()
        self._snapshot = PolicySnapshot()
        self._history: deque[ReloadResult] = deque(maxlen=max_history)
        # 最近一次 reload 调用的结果（含"无变化"与失败），供 /v1/policy 展示
        self._last: ReloadResult | None = None
        self._listeners: list[Callable[[ReloadResult], None]] = []
        if on_reload is not None:
            self._listeners.append(on_reload)

    # ---- 读取（每次读都拿最新快照） ----

    @property
    def snapshot(self) -> PolicySnapshot:
        with self._lock:
            return self._snapshot

    @property
    def rules(self) -> list[Rule]:
        return list(self.snapshot.rules)

    @property
    def tool_policy(self) -> ToolPolicy:
        return self.snapshot.tool_policy

    @property
    def tool_schemas(self) -> dict[str, dict[str, Any]]:
        return dict(self.snapshot.tool_schemas)

    @property
    def version(self) -> str:
        return self.snapshot.version

    @property
    def revision(self) -> int:
        return self.snapshot.revision

    @property
    def loaded_at(self) -> float:
        return self.snapshot.loaded_at

    @property
    def loaded(self) -> bool:
        return self.snapshot.loaded

    @property
    def errors(self) -> tuple[str, ...]:
        """最近一次 reload 的错误（成功则为空）。"""
        last = self.last_reload
        return last.errors if last else ()

    @property
    def last_reload(self) -> ReloadResult | None:
        """最近一次 reload 的结果（**包含**"无变化"与"失败"）。"""
        with self._lock:
            return self._last

    @property
    def history(self) -> list[ReloadResult]:
        """reload 历史（仅记"确实生效"与"失败回滚"），最新在前。"""
        with self._lock:
            return list(reversed(self._history))

    def clear_history(self) -> None:
        """清空 reload 历史与"最近一次 reload"记录。"""
        with self._lock:
            self._history.clear()
            self._last = None

    # ---- 监听器 ----

    def add_listener(self, fn: Callable[[ReloadResult], None]) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[ReloadResult], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    # ---- 扫描 ----

    def scan_files(self) -> list[Path]:
        """列出策略目录下的所有 YAML（.yaml/.yml），按文件名排序保证确定性。"""
        if not self.policy_dir.is_dir():
            return []
        found = {*self.policy_dir.glob("*.yaml"), *self.policy_dir.glob("*.yml")}
        return sorted(found, key=lambda p: p.name)

    # ---- 解析 ----

    @staticmethod
    def detect_roles(data: Mapping[str, Any]) -> tuple[str, ...]:
        """按顶层键判断一个 YAML 文件承担哪些角色。"""
        roles: list[str] = []
        if any(k in data for k in _RULE_KEYS):
            roles.append(ROLE_RULES)
        if any(k in data for k in _TOOL_POLICY_KEYS):
            roles.append(ROLE_TOOL_POLICY)
        if any(k in data for k in _SCHEMA_KEYS):
            roles.append(ROLE_TOOL_SCHEMAS)
        return tuple(roles)

    @classmethod
    def validate_yaml(cls, text: str) -> dict[str, Any]:
        """校验一段策略 YAML 是否合法（供 C4 / 在线编辑器预检，不落盘）。"""
        errors: list[str] = []
        warnings: list[str] = []
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return {
                "ok": False,
                "errors": [f"YAML 语法错误: {_one_line(exc)}"],
                "warnings": [],
                "roles": [],
            }
        if data is None:
            return {"ok": False, "errors": ["内容为空"], "warnings": [], "roles": []}
        if not isinstance(data, dict):
            return {
                "ok": False,
                "errors": [f"顶层必须是 mapping，实际是 {type(data).__name__}"],
                "warnings": [],
                "roles": [],
            }
        roles = list(cls.detect_roles(data))
        if not roles:
            warnings.append("未识别到任何策略段（rules / tool_policy / tool_schemas）")
        rule_ids: list[str] = []
        for item in data.get("rules") or []:
            try:
                r = Rule.from_dict(item)
                rule_ids.append(r.id)
            except RULE_PARSE_ERRORS as exc:
                errors.append(f"规则 {item.get('id')!r} 解析失败: {_one_line(exc)}")
        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "roles": roles,
            "rule_ids": rule_ids,
            "rule_count": len(rule_ids),
        }

    # ---- 主流程 ----

    def reload(self, *, trigger: str = "manual", force: bool = False) -> ReloadResult:
        """重新扫描并加载全部策略。

        Args:
            trigger: 触发来源，写入历史便于排查（startup / watcher / manual / api）。
            force: 即使内容未变也强制 bump 版本号。

        Returns:
            ReloadResult。``changed=False`` 表示内容未变或加载失败（看 ``errors``）。
        """
        started = time.perf_counter()
        with self._lock:
            prev = self._snapshot

            fingerprints: list[FileFingerprint] = []
            errors: list[str] = []
            warnings: list[str] = []
            rules_by_id: dict[str, Rule] = {}
            collected_patterns: list[Mapping[str, Any]] = []
            collected_blocked: list[str] = []
            collected_allow: list[str] = []
            schemas: dict[str, dict[str, Any]] = {}

            for path in self.scan_files():
                try:
                    raw = path.read_bytes()
                    data = yaml.safe_load(raw.decode("utf-8"))
                except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
                    errors.append(f"{path.name}: 读取/解析失败 - {_one_line(exc)}")
                    fingerprints.append(
                        FileFingerprint(name=path.name, path=str(path), sha256="", size=0, mtime=0.0)
                    )
                    continue

                if data is None:
                    data = {}
                if not isinstance(data, dict):
                    errors.append(
                        f"{path.name}: 顶层必须是 mapping，实际是 {type(data).__name__}"
                    )
                    continue

                roles = self.detect_roles(data)
                if not roles:
                    warnings.append(f"{path.name}: 未识别到策略段，已忽略")

                try:
                    stat = path.stat()
                except OSError:
                    stat = None
                fingerprints.append(
                    FileFingerprint(
                        name=path.name,
                        path=str(path),
                        sha256=_sha256(raw),
                        size=stat.st_size if stat else len(raw),
                        mtime=stat.st_mtime if stat else 0.0,
                        roles=roles,
                    )
                )

                # --- L3 规则 ---
                for item in data.get("rules") or []:
                    try:
                        rule = Rule.from_dict(item)
                    except RULE_PARSE_ERRORS as exc:
                        errors.append(
                            f"{path.name}: 规则 {item.get('id')!r} 解析失败 - {_one_line(exc)}"
                        )
                        continue
                    if rule.id in rules_by_id:
                        warnings.append(
                            f"{path.name}: 规则 id {rule.id!r} 与其他文件重复，后者覆盖前者"
                        )
                    rules_by_id[rule.id] = rule

                # --- L4 工具策略 ---
                for key, bucket in (
                    ("blocked_tools", collected_blocked),
                    ("allowlist", collected_allow),
                ):
                    vals = data.get(key)
                    if isinstance(vals, list):
                        bucket.extend(str(v) for v in vals)
                for pat in data.get("dangerous_patterns") or []:
                    if isinstance(pat, dict) and "name" in pat and "pattern" in pat:
                        collected_patterns.append(pat)
                    elif pat is not None:
                        errors.append(f"{path.name}: dangerous_patterns 项缺少 name/pattern")

                # --- 工具 Schema ---
                ts = data.get("tool_schemas")
                if isinstance(ts, dict):
                    schemas.update(
                        {str(k).lower(): v for k, v in ts.items() if isinstance(v, dict)}
                    )
                elif ts is not None:
                    errors.append(f"{path.name}: tool_schemas 必须是 mapping")

            files_map = {f.name: f for f in fingerprints}
            now = time.time()

            # ---- 失败整体回滚：保留上一版 ----
            if errors:
                result = ReloadResult(
                    changed=False,
                    revision=prev.revision,
                    version=prev.version,
                    previous_version=prev.version,
                    trigger=trigger,
                    at=now,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    files_total=len(fingerprints),
                    errors=tuple(errors),
                    warnings=tuple(warnings),
                    degraded=True,
                )
                self._history.append(result)
                self._last = result
                logger.error("策略 reload 失败，保留上一版 r%s: %s", prev.revision, errors[0])
                self._notify(result)
                return result

            new_version = self._compute_version(fingerprints)

            # ---- 内容未变：不 bump 版本、不写历史 ----
            if not force and prev.loaded and new_version == prev.version:
                unchanged = ReloadResult(
                    changed=False,
                    revision=prev.revision,
                    version=prev.version,
                    previous_version=prev.version,
                    trigger=trigger,
                    at=now,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    files_total=len(fingerprints),
                    warnings=tuple(warnings),
                )
                self._last = unchanged
                return unchanged

            prev_files = {f.name: f for f in prev.files}
            prev_rule_hashes = {r.id: _rule_hash(r) for r in prev.rules}
            new_rule_hashes = {r.id: _rule_hash(r) for r in rules_by_id.values()}

            added_rules = tuple(sorted(set(new_rule_hashes) - set(prev_rule_hashes)))
            removed_rules = tuple(sorted(set(prev_rule_hashes) - set(new_rule_hashes)))
            changed_rules = tuple(
                sorted(
                    rid
                    for rid in set(prev_rule_hashes) & set(new_rule_hashes)
                    if prev_rule_hashes[rid] != new_rule_hashes[rid]
                )
            )

            tool_policy = ToolPolicy.from_dict(
                {
                    "blocked_tools": _dedupe(collected_blocked),
                    "allowlist": _dedupe(collected_allow),
                    "dangerous_patterns": _dedupe_patterns(collected_patterns),
                }
            )

            snapshot = PolicySnapshot(
                revision=prev.revision + 1,
                version=new_version,
                loaded_at=now,
                rules=tuple(rules_by_id.values()),
                tool_policy=tool_policy,
                tool_schemas=schemas,
                files=tuple(fingerprints),
                warnings=tuple(warnings),
            )

            result = ReloadResult(
                changed=True,
                revision=snapshot.revision,
                version=new_version,
                previous_version=prev.version if prev.loaded else None,
                trigger=trigger,
                at=now,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                files_total=len(fingerprints),
                added_files=tuple(sorted(set(files_map) - set(prev_files))),
                removed_files=tuple(sorted(set(prev_files) - set(files_map))),
                changed_files=tuple(
                    sorted(
                        name
                        for name in set(prev_files) & set(files_map)
                        if prev_files[name].sha256 != files_map[name].sha256
                    )
                ),
                added_rules=added_rules,
                removed_rules=removed_rules,
                changed_rules=changed_rules,
                warnings=tuple(warnings),
            )

            self._snapshot = snapshot
            self._history.append(result)
            self._last = result

        logger.info("策略热更新生效 · %s", result.summary())
        self._notify(result)
        return result

    def _compute_version(self, fingerprints: Sequence[FileFingerprint]) -> str:
        h = hashlib.sha256()
        for fp in sorted(fingerprints, key=lambda f: f.name):
            h.update(fp.name.encode("utf-8"))
            h.update(b"\x00")
            h.update(fp.sha256.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()[:12]

    def _notify(self, result: ReloadResult) -> None:
        for fn in list(self._listeners):
            try:
                fn(result)
            except Exception:  # pragma: no cover - 监听器异常不影响主流程
                logger.exception("策略 reload 监听器执行失败")


__all__ = [
    "ROLE_RULES",
    "ROLE_TOOL_POLICY",
    "ROLE_TOOL_SCHEMAS",
    "FileFingerprint",
    "PolicyRegistry",
    "PolicySnapshot",
    "ReloadResult",
    "default_policy_dir",
]
