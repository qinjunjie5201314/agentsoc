"""集成测试 - 验证 Alembic 迁移可重放。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture()
def temp_db(monkeypatch, tmp_path: Path) -> Path:
    """用临时路径初始化数据库，并跑迁移。"""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # 子进程跑 alembic，避免污染模块级 engine
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"alembic 失败:\n{result.stderr}"
    return db_path


def test_migrations_create_three_tables(temp_db: Path) -> None:
    """三张核心表存在。"""
    import sqlalchemy as sa

    engine = sa.create_engine(f"sqlite:///{temp_db}")
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }

    assert {"sessions", "risk_events", "audit_logs", "alembic_version"} <= tables


def test_migrations_create_indexes(temp_db: Path) -> None:
    """7 个业务索引全部创建。"""
    import sqlalchemy as sa

    engine = sa.create_engine(f"sqlite:///{temp_db}")
    with engine.connect() as conn:
        idx = {
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND name NOT LIKE 'sqlite_%'"
                )
            )
        }

    expected = {
        "ix_sessions_user_id",
        "ix_sessions_agent_id",
        "ix_risk_events_session_id",
        "ix_risk_events_created_at",
        "ix_audit_logs_session_id",
        "ix_audit_logs_event_type",
        "ix_audit_logs_created_at",
    }
    assert expected <= idx


def test_migrations_idempotent(temp_db: Path) -> None:
    """二次 upgrade head 不报错（已 up-to-date）。"""
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{temp_db}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Running upgrade" not in result.stdout or "Running upgrade -> " in result.stdout
