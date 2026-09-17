"""配置模块测试。"""

from app.config import settings


def test_settings_default():
    """默认配置可加载。"""
    assert settings.app_name == "AgentSoc"
    assert settings.port == 8000


def test_settings_is_dev():
    """dev 环境判断。"""
    assert settings.is_dev is True


def test_settings_is_sqlite():
    """SQLite URL 判断。"""
    assert settings.is_sqlite is True


def test_settings_llm_backend_default():
    """阶段 3：默认后端是 mock。"""
    assert settings.llm_backend == "mock"


def test_settings_remote_classifier_defaults():
    """阶段 3：远程判别模型默认配置可读。"""
    assert settings.classifier_remote_base_url == ""
    assert settings.classifier_remote_model == "gpt-4o-mini"
    assert settings.classifier_remote_timeout == 5.0
