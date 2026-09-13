.PHONY: help install run dev test test-unit test-int lint fmt clean demo demo-policy up down logs

help:  ## 显示帮助
	@echo "AgentSoc - 可用命令:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## 安装依赖
	pip install -e ".[dev]"

run:  ## 生产模式启动
	uvicorn app.main:app --host 0.0.0.0 --port 8000

dev:  ## 开发模式（热重载）
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test:  ## 跑全部测试
	pytest

test-unit:  ## 只跑单元测试
	pytest tests/unit -v

test-int:  ## 只跑集成测试
	pytest tests/integration -v

lint:  ## 代码检查
	ruff check app tests

fmt:  ## 代码格式化
	ruff format app tests

clean:  ## 清理缓存
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -f agentsentry.db

demo:  ## 一键启动 + 跑攻击演示
	docker compose up -d
	@echo "等待服务启动..."
	@sleep 3
	@curl -s http://localhost:8000/health | python -m json.tool

demo-policy:  ## 跑 C3 策略配置中心 + 热更新命令行演示
	python examples/demo_policy_hot_reload.py

demo-tools:  ## 跑 C2 工具调用 Hook 命令行演示
	python examples/demo_tool_hook.py

dashboard:  ## D2 终端实时风险事件流（Ctrl+C 退出）
	python -m app.cli.dashboard

dashboard-once:  ## D2 终端风险事件快照
	python -m app.cli.dashboard --once

up:  ## Docker Compose 启动
	docker compose up -d

down:  ## Docker Compose 停止
	docker compose down

logs:  ## 查看 Docker 日志
	docker compose logs -f

db-upgrade:  ## 应用数据库迁移到最新版本
	alembic upgrade head

db-downgrade:  ## 回滚一次迁移
	alembic downgrade -1

db-revision:  ## 自动生成新迁移（用法: make db-revision msg="add foo column"）
	alembic revision --autogenerate -m "$(msg)"
