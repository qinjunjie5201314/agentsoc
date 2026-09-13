"""配置加载 - pydantic-settings 从环境变量/.env 读取。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 服务
    app_name: str = "AgentSoc"
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    # 数据库
    database_url: str = "sqlite:///./agentsentry.db"

    # Redis（M2 启用）
    redis_url: str = "redis://localhost:6379/0"

    # LLM Provider
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"

    # 判别模型
    #   disabled → 空判别器（零分，只靠规则引擎）· M1 默认
    #   local    → 本地 HuggingFace 模型（deberta-v3）
    #   remote   → 远程 API（stub，M2 实装）
    classifier_mode: Literal["local", "remote", "disabled"] = "disabled"
    classifier_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    # 本地模型配置（classifier_mode=local 时生效）
    classifier_model_name: str = "protectai/deberta-v3-base-prompt-injection-v2"
    classifier_device: str = "cpu"  # "cpu" | "cuda" | "mps"
    classifier_cache_dir: str = ""  # 模型缓存目录；空则用 HF 默认缓存

    # 策略
    policy_dir: Path = Path("./policies")
    policy_reload_interval: int = 2  # 秒；YAML 改动后最多该秒数内自动生效

    # 审计
    audit_enabled: bool = True
    audit_retention_days: int = 90

    # 安全：管理端点鉴权（设了 API_KEY 后，/v1/policy/*、/v1/audit/* 需带 X-API-Key 头）
    api_key: str = ""

    @property
    def admin_auth_enabled(self) -> bool:
        """是否启用管理端点鉴权。"""
        return bool(self.api_key)

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


# 全局单例
settings = Settings()
