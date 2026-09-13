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
