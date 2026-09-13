"""监控指标 - 单元测试。"""
from __future__ import annotations

from app.monitoring.metrics import Metrics


class TestMetrics:
    def test_incr_and_render(self):
        m = Metrics()
        m.incr("test_total", {"action": "block"})
        m.incr("test_total", {"action": "block"})
        out = m.render()
        assert 'test_total{action="block"} 2' in out

    def test_observe_sum_count(self):
        m = Metrics()
        m.observe("test_ms", 10.5)
        m.observe("test_ms", 20.5)
        out = m.render()
        assert "test_ms_sum" in out
        assert "test_ms_count 2" in out

    def test_gauge(self):
        m = Metrics()
        m.set_gauge("test_gauge", 42)
        assert "test_gauge 42" in m.render()

    def test_record_request(self):
        m = Metrics()
        m.record_request("block", 3.2)
        out = m.render()
        assert 'agentsoc_requests_total{action="block"} 1' in out
        assert "agentsoc_detection_ms_sum" in out

    def test_record_classifier(self):
        m = Metrics()
        m.record_classifier("local", 12.0)
        out = m.render()
        assert 'agentsoc_classifier_ms_count{provider="local"} 1' in out

    def test_record_tool_call(self):
        m = Metrics()
        m.record_tool_call("blocked")
        m.record_tool_call("executed")
        out = m.render()
        assert 'agentsoc_tool_calls_total{status="blocked"} 1' in out
        assert 'agentsoc_tool_calls_total{status="executed"} 1' in out

    def test_uptime_present(self):
        m = Metrics()
        assert "agentsoc_uptime_seconds" in m.render()

    def test_label_sorting_stable(self):
        m = Metrics()
        m.incr("x_total", {"b": "2", "a": "1"})
        # 标签应按字母序 a,b 输出
        assert 'x_total{a="1",b="2"} 1' in m.render()
