"""数据库连接与 Session 管理。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _build_engine(url: str) -> Engine:
    """根据 URL 构造 Engine，SQLite 需要特殊 connect_args。"""
    if url.startswith("sqlite"):
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            echo=False,
        )
    return create_engine(url, echo=False, pool_pre_ping=True)


engine: Engine = _build_engine(settings.database_url)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入用的 Session 生成器。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本/后台任务用的 Session 上下文管理器（自动 commit/rollback）。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """首次启动时建表（M1 阶段直接 create_all，M2 切到 alembic）。"""
    from app.audit import models  # noqa: F401  确保模型被注册
    from app.policy import change_log  # noqa: F401  策略变更台账表

    Base.metadata.create_all(bind=engine)
    logger.info("数据库初始化完成 · url=%s", settings.database_url)
