"""FastAPI 入口。"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request

from app import __version__
from app.audit import AuditLogger, create_audit_router
from app.config import settings
from app.detection.classifier import get_classifier
from app.detection.pipeline import DetectionPipeline
from app.logging_config import setup_logging
from app.security import require_admin
from app.policy import (
    PolicyRegistry,
    PolicyWatcher,
    create_approval_router,
    create_nl_router,
    create_policy_router,
)
from app.proxy.demo_dashboard_page import create_dashboard_router
from app.proxy.demo_audit_page import create_audit_demo_router
from app.proxy.demo_nl_page import create_nl_demo_router
from app.proxy.demo_page import create_demo_router
from app.proxy.demo_policy_page import create_policy_demo_router
from app.proxy.demo_tool_page import create_tool_demo_router
from app.proxy.openai_proxy import create_proxy_router
from app.proxy.tool_hook import ToolGuard
from app.proxy.tool_routes import create_tool_router

setup_logging()
logger = logging.getLogger(__name__)


# ---- C3: 策略配置中心（进程级单例，pipeline / guard / watcher 共享） ----

_registry = PolicyRegistry(settings.policy_dir)
_startup_reload = _registry.reload(trigger="startup")
if not _startup_reload.ok:
    logger.error("启动时策略加载失败: %s", _startup_reload.errors)
else:
    logger.info(
        "策略已加载 · r%d / %s · %d 条规则 · %d 个文件",
        _registry.revision,
        _registry.version,
        _registry.snapshot.rule_count,
        _registry.snapshot.file_count,
    )

_watcher = PolicyWatcher(_registry, interval=settings.policy_reload_interval)

# ---- D1: 审计落库器（进程级单例，异步 flush） ----
_audit = AuditLogger(enabled=settings.audit_enabled)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭钩子。"""
    logger.info("AgentSoc 启动 · env=%s · version=%s", settings.app_env, __version__)
    # 首次启动时自动建表（M1 简化：直接 create_all；M2 切到 alembic check + 自动 upgrade）
    try:
        from app.db import init_db

        init_db()
    except (ImportError, RuntimeError) as exc:
        logger.warning("数据库初始化失败（可忽略）: %s", exc)
    # C3：启动策略目录监听（YAML 改动 interval 秒内自动生效）
    _watcher.start()
    # D1：启动审计后台 flush 线程
    _audit.start()
    yield
    _audit.shutdown()
    _watcher.stop()
    logger.info("AgentSoc 关闭")


app = FastAPI(
    title=settings.app_name,
    description="AI 助手安全防护 - L1-L4 纵深防御 MVP",
    version=__version__,
    lifespan=lifespan,
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """访问日志中间件。"""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s -> %d (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """健康检查。"""
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
        "env": settings.app_env,
    }


@app.get("/", tags=["meta"])
async def root() -> dict:
    """根路径。"""
    return {
        "service": settings.app_name,
        "version": __version__,
        "docs": "/docs",
    }


@app.get("/v1/info", tags=["meta"])
async def info() -> dict:
    """服务元信息（含 C3 策略版本）。"""
    return {
        "version": __version__,
        "env": settings.app_env,
        "classifier_mode": settings.classifier_mode,
        "policy_dir": str(settings.policy_dir),
        "policy_revision": _registry.revision,
        "policy_version": _registry.version,
        "policy_rule_count": _registry.snapshot.rule_count,
        "policy_watcher_running": _watcher.running,
        "policy_reload_interval_s": settings.policy_reload_interval,
        "audit_enabled": settings.audit_enabled,
    }


@app.get("/metrics", tags=["meta"])
async def metrics_endpoint() -> Any:
    """Prometheus 格式监控指标。"""
    from fastapi.responses import PlainTextResponse

    from app.monitoring.metrics import metrics

    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


@app.get("/healthz", tags=["meta"])
async def readiness() -> dict:
    """就绪探针：策略已加载 + 审计线程运行即就绪。"""
    ready = _registry.loaded
    return {
        "status": "ok" if ready else "degraded",
        "policy_loaded": ready,
        "policy_revision": _registry.revision,
        "audit_running": _audit.stats["running"],
    }


# ---- C1: OpenAI 兼容 Proxy + 可视化 Demo ----
def _build_classifier():
    """按 settings.classifier_mode 构建判别器；失败优雅降级到 disabled（不崩溃）。"""
    mode = settings.classifier_mode
    try:
        if mode == "local":
            kwargs: dict[str, Any] = {
                "model_name": settings.classifier_model_name,
                "device": settings.classifier_device,
            }
            if settings.classifier_cache_dir:
                kwargs["cache_dir"] = settings.classifier_cache_dir
            return get_classifier("local", **kwargs)
        if mode == "remote":
            return get_classifier("remote")
        return get_classifier("disabled")
    except Exception as exc:  # noqa: BLE001 - 模型加载失败降级，不阻断服务
        logger.warning("判别模型初始化失败 (%s)，降级为 disabled（纯规则引擎）: %s", mode, exc)
        return get_classifier("disabled")


_classifier = _build_classifier()

# C3: pipeline 与 guard 都挂到同一个 registry 上，策略热更新对二者同时生效
_pipeline: DetectionPipeline = DetectionPipeline(
    _registry.rules,
    classifier=_classifier,
    registry=_registry,
)
_tool_guard = ToolGuard(registry=_registry)

app.include_router(create_proxy_router(_pipeline, tool_guard=_tool_guard, audit=_audit))
app.include_router(create_tool_router(guard=_tool_guard))
# 管理/审计端点加鉴权（设了 API_KEY 才生效；未设则开放，方便 dev 调试）
app.include_router(
    create_policy_router(registry=_registry, watcher=_watcher),
    dependencies=[Depends(require_admin)],
)
app.include_router(
    create_nl_router(registry=_registry, policy_dir=_registry.policy_dir),
    dependencies=[Depends(require_admin)],
)
app.include_router(create_audit_router(audit=_audit), dependencies=[Depends(require_admin)])
app.include_router(
    create_approval_router(registry=_registry),
    dependencies=[Depends(require_admin)],
)
app.include_router(create_dashboard_router())
app.include_router(create_audit_demo_router())
app.include_router(create_demo_router())
app.include_router(create_tool_demo_router())
app.include_router(create_policy_demo_router(registry=_registry, watcher=_watcher))
app.include_router(create_nl_demo_router())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.is_dev,
    )
