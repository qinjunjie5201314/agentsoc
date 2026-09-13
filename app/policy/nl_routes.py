"""C4 NL 转策略 HTTP 端点。

端点:
    POST /v1/policy/nl/preview     只生成草稿，不落盘
    POST /v1/policy/nl/apply       校验通过后写入策略目录 + 触发 reload
    GET  /v1/policy/nl/examples    预置示例
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.policy.nl2policy import EXAMPLES, MIN_CONFIDENCE, Intent, nl_to_draft
from app.policy.registry import PolicyRegistry

# =========================== 请求 / 响应 ===========================


class NLPreviewRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    severity: str = Field(default="high", pattern=r"^(low|medium|high|critical)$")


class NLApplyRequest(NLPreviewRequest):
    """preview + 落盘参数。"""

    filename: str | None = Field(default=None, max_length=64)
    force_reload: bool = Field(default=True)


def _err(detail: str, status_code: int = 400) -> None:
    raise HTTPException(status_code=status_code, detail=detail)


# =========================== 路由工厂 ===========================


def create_nl_router(
    *,
    registry: PolicyRegistry,
    policy_dir: Path,
) -> APIRouter:
    """NL → 策略的 HTTP 路由。"""
    router = APIRouter(tags=["policy", "nl2policy"])

    @router.get("/v1/policy/nl/examples")
    async def nl_examples() -> dict[str, Any]:
        return {
            "count": len(EXAMPLES),
            "min_confidence": MIN_CONFIDENCE,
            "examples": EXAMPLES,
        }

    @router.post("/v1/policy/nl/preview")
    async def nl_preview(req: NLPreviewRequest) -> dict[str, Any]:
        try:
            draft = nl_to_draft(req.text, registry=registry, severity=req.severity)
        except ValueError as exc:
            _err(str(exc), status_code=400)
        return {
            "ok": draft.validation.get("ok", False),
            "draft": draft.to_dict(),
        }

    @router.post("/v1/policy/nl/apply")
    async def nl_apply(req: NLApplyRequest) -> dict[str, Any]:
        try:
            draft = nl_to_draft(req.text, registry=registry, severity=req.severity)
        except ValueError as exc:
            _err(str(exc), status_code=400)

        cls_intent = draft.classification.intent
        if cls_intent is Intent.UNCERTAIN:
            _err(
                f"NL 不可解析：{draft.classification.reason}",
                status_code=422,
            )
        if not draft.validation.get("ok", False):
            errs = draft.validation.get("errors") or ["校验失败"]
            _err("生成的 YAML 预检未通过：" + "; ".join(errs), status_code=422)

        # 写入策略目录
        fname = (req.filename or draft.filename_suggestion).strip()
        # 防路径穿越
        if "/" in fname or "\\" in fname or not fname.endswith(".yaml"):
            _err(f"非法 filename: {fname!r}（需为简单 .yaml 文件名）", status_code=400)
        if not re.match(r"^[A-Za-z0-9._\-]+$", fname):
            _err(f"filename 仅允许字母数字 _ . -: {fname!r}", status_code=400)
        target = (policy_dir / fname).resolve()
        if policy_dir.resolve() not in target.parents and target != policy_dir:
            _err("filename 越界", status_code=400)

        target.write_text(draft.yaml_text, encoding="utf-8")

        result = (
            registry.reload(trigger="nl_apply", force=req.force_reload)
            if req.force_reload
            else None
        )

        return {
            "ok": True,
            "filename": fname,
            "path": str(target),
            "intent": cls_intent.value,
            "confidence": round(draft.classification.confidence, 2),
            "summary": draft.summary,
            "reload": {
                "triggered": bool(req.force_reload),
                "revision": result.revision if result else None,
                "version": result.version if result else None,
                "ok": result.ok if result else None,
                "errors": list(result.errors) if result else [],
            },
        }

    return router


__all__ = ["NLApplyRequest", "NLPreviewRequest", "create_nl_router"]
