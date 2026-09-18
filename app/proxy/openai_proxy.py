"""C1 OpenAI 兼容 Proxy 路由（含 C2 工具调用 Hook）。

职责：
  1. 暴露 ``POST /v1/chat/completions`` 端点（OpenAI 兼容，支持 stream）
  2. 入口处调 ``DetectionPipeline.run()`` 对 messages 做 L1-L3 检测
  3. 按 ``final_action`` 路由：
     - block → 返回 HTTP 400 + 详细原因（不再调用 LLM）
     - confirm → M1 简化：同样 block（标注 reason=requires_confirm，后续 M2 接 UI 二次确认）
     - allow → 透传到 mock LLM（M2 替换为真实后端）
  4. **C2**：LLM 返回 ``tool_calls`` 时，先过 ``ToolGuard``（L4 egress 兜底），
     被拦的工具绝不执行，转成 tool_result 错误回灌给模型，让模型解释而不是静默失败。

为什么拦截统一用 400：OpenAI 客户端普遍把 400 当"参数错误"，拦截时给 400 + 详细
reason 最方便调用方排查；403/401 留给业务权限语义。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.audit.models import RiskAction, RiskLevel, SourceType
from app.detection.isolate import tag_messages
from app.detection.normalize import normalize
from app.detection.pipeline import DetectionPipeline
from app.proxy.llm_backend import MockLLMBackend
from app.proxy.schemas import (
    AgentsentryErrorBody,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from app.proxy.tool_executor import DryRunToolExecutor
from app.proxy.tool_hook import (
    ToolGuard,
    build_tool_result_error,
    build_tool_result_ok,
    extract_tool_calls,
)

logger = logging.getLogger(__name__)


# 错误码常量（OpenAI 风格）
ERR_BLOCKED = "agentsentry_request_blocked"
ERR_REQUIRES_CONFIRM = "agentsentry_request_requires_confirm"
ERR_TOOL_BLOCKED = "agentsentry_tool_call_blocked"
ERR_BAD_REQUEST = "invalid_request_error"


def _l1_preview(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对 user/tool 段做 L1 归一化预览，供 UI 展示"原始 → 归一化"对比。"""
    out: list[dict[str, Any]] = []
    for t in tag_messages(messages):
        if t.source in (SourceType.USER, SourceType.TOOL):
            norm = normalize(t.content) or t.content
            changed = norm != t.content
            out.append(
                {
                    "source": t.source.value,
                    "original_preview": t.content[:200],
                    "normalized_preview": norm[:200] if changed else None,
                    "changed": changed,
                }
            )
    return out


def _summary_payload(
    result,
    messages: list[dict[str, Any]] | None = None,
    tools_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把检测结果压成可附加在响应/日志里的精简 dict。"""
    d = result.to_dict()
    action = result.final_action
    risk_level = result.risk_level

    # C2：工具层被拦 → 整体动作升级为 block（即使 prompt 层是 allow）
    if tools_meta and tools_meta.get("blocked_count"):
        action = RiskAction.BLOCK
        risk_level = RiskLevel.HIGH

    d["action"] = action.value
    d["risk_level"] = risk_level.value
    if messages is not None:
        d["l1_preview"] = _l1_preview(messages)
    if tools_meta is not None:
        d["tools"] = tools_meta
        if tools_meta.get("blocked_count"):
            d["tool_blocked"] = True
    return d


def _block_response(result, reason_code: str, http_status: int) -> JSONResponse:
    """构造被拦截时的统一错误体。"""
    f = result.fused
    reason_text = "; ".join(f.reasons) if f and f.reasons else ""
    body = {
        "error": {
            "code": reason_code,
            "message": reason_text or f"被 {result.final_action.value}（risk={result.risk_level.value}）",
            "type": ERR_BAD_REQUEST,
            "param": "messages",
        },
        "agentsentry": {
            "risk_level": result.risk_level.value,
            "action": result.final_action.value,
            "policy": (
                {"version": result.policy_version, "revision": result.policy_revision}
                if result.policy_version
                else None
            ),
            "rule_hits": [
                {
                    "rule_id": h.rule_id,
                    "severity": h.severity.value,
                    "matched_text": h.matched_text,
                    "source": h.source.value,
                }
                for h in result.hits
            ],
            "classifier_max_score": round(result.max_score, 4),
            "reasons": f.reasons if f else [],
            "fused": f.to_dict() if f else None,
        },
    }
    return JSONResponse(status_code=http_status, content=body)


def _process_tool_calls(
    *,
    guard: ToolGuard,
    executor: DryRunToolExecutor,
    backend: Any,
    model: str,
    messages: list[dict[str, Any]],
    first_response: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """C2 核心：guard 工具调用 → 执行/拦截 → 回灌 → 第二轮回复。

    ``backend`` 是放行后的真实 LLM 后端（有 chat_completion 方法）。

    Returns:
        (最终响应, C2 元信息)。无工具调用时原样返回 (first_response, None)。
    """
    calls = extract_tool_calls(first_response, protocol="openai")
    if not calls:
        return first_response, None

    guard_result = guard.guard_calls(calls)
    assistant_msg = first_response["choices"][0]["message"]

    tool_msgs: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []

    for guarded in guard_result.guarded:
        if guarded.blocked:
            # 被拦：绝不执行，转成 error tool_result 回灌给模型
            tool_msgs.append(build_tool_result_error(guarded))
            executions.append(
                {
                    "name": guarded.call.name,
                    "call_id": guarded.call.call_id,
                    "status": "blocked",
                    "executed": False,
                    "arguments": guarded.call.arguments,
                    "reason": guarded.decision.reason,
                    "checks": [c.name for c in guarded.decision.checks if not c.passed],
                }
            )
            logger.warning(
                "C2 拦截工具调用 · tool=%s · reason=%s",
                guarded.call.name,
                guarded.decision.reason[:160],
            )
        else:
            er = executor.execute(guarded.call.name, guarded.call.arguments)
            tool_msgs.append(
                build_tool_result_ok(guarded, DryRunToolExecutor.as_text(er))
            )
            executions.append(
                {
                    "name": guarded.call.name,
                    "call_id": guarded.call.call_id,
                    "status": "executed" if er.ok else "failed",
                    "executed": True,
                    "arguments": guarded.call.arguments,
                    "dry_run": er.dry_run,
                    "output": er.output,
                    "error": er.error,
                }
            )

    # 第二轮：把 tool 结果（含拦截错误）回灌，让模型给出解释性回复
    followup_messages = [*messages, assistant_msg, *tool_msgs]
    followup = backend.chat_completion(model=model, messages=followup_messages)

    meta: dict[str, Any] = {
        **guard_result.to_dict(),
        "executions": executions,
        "rounds": 2,
        "blocked_by_l4": bool(guard_result.blocked),
    }
    return followup, meta


def create_proxy_router(
    pipeline: DetectionPipeline | None = None,
    *,
    tool_guard: ToolGuard | None = None,
    executor: DryRunToolExecutor | None = None,
    auto_handle_tools: bool = True,
    audit: Any | None = None,
    backend: Any | None = None,
) -> APIRouter:
    """创建 Proxy 路由。Pipeline / Guard / Executor / AuditLogger / LLM backend 均可注入。

    ``backend`` 是放行后真正调用的 LLM 后端（阶段 3 试点接内网网关时传入
    OpenAICompatBackend；默认 MockLLMBackend 回显）。
    """
    pipe = pipeline or DetectionPipeline.from_yaml("policies/builtin_rules.yaml")
    llm_backend = backend or MockLLMBackend()
    guard = tool_guard or ToolGuard()
    ex = executor or DryRunToolExecutor()
    audit_logger = audit  # 可空：审计关闭时跳过落库
    router = APIRouter(tags=["proxy"])

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        started = time.perf_counter()
        # 解析 body（不强校验，野客户端也能跑）
        try:
            raw_body = await request.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"请求体不是合法 JSON: {exc}") from exc

        try:
            req = ChatCompletionRequest.model_validate(raw_body)
        except (ValueError, TypeError) as exc:  # 校验失败也尽量走通
            logger.warning("ChatCompletionRequest 校验失败，使用原始 body 继续: %s", exc)
            req = ChatCompletionRequest(
                model=str(raw_body.get("model", "gpt-4o-mini")),
                messages=raw_body.get("messages", []),
                stream=bool(raw_body.get("stream", False)),
                tools=raw_body.get("tools"),
            )

        # 兜底：客户端/demo 页可能传占位 model（如 "gpt-4o-mini"），
        # 但真实后端只认配置的 LLM_MODEL（如 deepseek-v4-pro）。
        # 这里把 model 归一化到 settings.llm_model，避免透传错模型导致上游 503。
        if not req.model or req.model == "gpt-4o-mini":
            req.model = settings.llm_model

        messages = req.messages or []
        if not messages:
            raise HTTPException(status_code=400, detail="messages 不能为空")

        # ---- L1 + L2 + L3 (含 B5 融合) ----
        result = pipe.run(messages, session_id=req.session_id)
        elapsed = (time.perf_counter() - started) * 1000
        logger.info(
            "proxy request · action=%s · risk=%s · hits=%d · %.1fms",
            result.final_action.value,
            result.risk_level.value,
            len(result.hits),
            elapsed,
        )

        # ---- 监控指标：请求动作 + 检测耗时 ----
        try:
            from app.monitoring.metrics import metrics

            metrics.record_request(result.final_action.value, elapsed)
            # 判别模型耗时（若有 classifier 评分）
            for s in getattr(result, "classifier_scores", []) or []:
                metrics.record_classifier(s.provider, s.elapsed_ms)
        except Exception:  # noqa: BLE001 - 指标失败不影响主链路
            pass

        # ---- D1：审计落库（失败不反噬主链路） ----
        if audit_logger is not None:
            try:
                audit_logger.log_detection(
                    session_id=req.session_id or f"anon-{uuid.uuid4().hex[:12]}",
                    result=result,
                    messages=messages,
                )
            except Exception as exc:  # noqa: BLE001 - 审计失败仅记 warning
                logger.warning("审计落库失败（忽略）: %s", exc)

        # ---- 路由 ----
        if result.final_action is RiskAction.BLOCK:
            return _block_response(result, ERR_BLOCKED, http_status=400)

        if result.final_action is RiskAction.CONFIRM:
            # M1 简化：confirm 也按 block 处理
            return _block_response(result, ERR_REQUIRES_CONFIRM, http_status=400)

        # ---- allow：透传到 LLM 后端（mock 或内网网关） ----
        # 先探测本轮是否会产生工具调用（mock 是确定性的，真实后端此探测会多一次调用；
        # 试点阶段为保持 C2 工具拦截逻辑一致，沿用探测，M2 再优化为流式增量）
        probe = llm_backend.chat_completion(model=req.model, messages=messages, tools=req.tools)
        has_tool_calls = bool(extract_tool_calls(probe, protocol="openai"))

        if auto_handle_tools and has_tool_calls:
            final, tools_meta = _process_tool_calls(
                guard=guard,
                executor=ex,
                backend=llm_backend,
                model=req.model,
                messages=messages,
                first_response=probe,
            )
            # D1：落库 L4 工具调用事件 + 监控指标
            if tools_meta is not None:
                try:
                    from app.monitoring.metrics import metrics

                    for exe in tools_meta.get("executions", []):
                        status = exe.get("status", "executed")
                        metrics.record_tool_call(status)
                except Exception:  # noqa: BLE001
                    pass
            if audit_logger is not None and tools_meta is not None:
                try:
                    for exe in tools_meta.get("executions", []):
                        audit_logger.log_tool_call(
                            session_id=req.session_id or "anon",
                            tool_name=exe.get("name", "unknown"),
                            arguments=exe.get("arguments", {}),
                            status=exe.get("status", "executed"),
                            reason=exe.get("reason", ""),
                            dry_run=exe.get("dry_run", True),
                            policy_version=result.policy_version,
                            policy_revision=result.policy_revision,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("L4 审计落库失败（忽略）: %s", exc)
            # 流式请求遇到工具调用时降级为非流式 JSON（M1 简化；M2 支持流式 tool_calls）
            final["agentsentry"] = _summary_payload(result, messages, tools_meta)
            status = 400 if (tools_meta or {}).get("blocked_count") else 200
            if status == 400:
                # 工具被拦：返回结构化错误体（便于调用方 fail-fast），同时带上模型解释
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "code": ERR_TOOL_BLOCKED,
                            "message": (tools_meta or {}).get("reason", "工具调用被拦截"),
                            "type": ERR_BAD_REQUEST,
                            "param": "tool_calls",
                        },
                        "agentsentry": final["agentsentry"],
                        "assistant_explanation": (
                            final.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "")
                        ),
                    },
                )
            return JSONResponse(status_code=200, content=final)

        if req.stream:
            return StreamingResponse(
                _sse_iter(llm_backend, req, _summary_payload(result, messages)),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        resp = probe
        resp["agentsentry"] = _summary_payload(result, messages)
        return JSONResponse(content=resp)

    return router


async def _sse_iter(backend: Any, req: ChatCompletionRequest, agentsentry_meta: dict) -> Any:
    """SSE 事件流。``backend`` 需提供 stream_chat_completion。"""
    for chunk in backend.stream_chat_completion(
        model=req.model, messages=req.messages, tools=req.tools
    ):
        # 首个含 choices 的 chunk 附加 agentsentry 元信息
        if chunk.get("choices") and "agentsentry" not in chunk:
            chunk["agentsentry"] = agentsentry_meta
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


__all__ = [
    "AgentsentryErrorBody",
    "ChatCompletionResponse",
    "create_proxy_router",
]
