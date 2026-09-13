"""安全自证 —— 管理端点鉴权。

设计：
  - 设了 ``API_KEY`` 环境变量后，管理端点（/v1/policy/*、/v1/audit/*）需要
    请求头带 ``X-API-Key: <key>`` 才能访问。
  - 没设 ``API_KEY`` 时（默认），管理端点开放（dev 环境方便调试）。
  - 用 ``secrets.compare_digest`` 做常量时间比较，防时序侧信道。

注意：这只保护「管理/审计」端点（读敏感日志、改策略）。
``/v1/chat/completions``（业务转发端点）不受此鉴权影响，它有自己的检测逻辑。
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request

from app.config import settings


async def require_api_key(request: Request) -> None:
    """FastAPI 依赖：校验 X-API-Key 头。未启用鉴权时直接放行。"""
    if not settings.admin_auth_enabled:
        return
    provided = request.headers.get("X-API-Key", "")
    if not provided or not secrets.compare_digest(provided, settings.api_key):
        raise HTTPException(status_code=401, detail="缺少或错误的 API Key")


# 便捷依赖别名（供路由用 Depends(require_admin)）
require_admin = require_api_key

__all__ = ["require_api_key", "require_admin"]
