"""结构分层测试。"""

from tesla_notifier.analytics.traffic import TrafficSampler
from tesla_notifier.integrations.weather import get_weather
from tesla_notifier.notifications import bark
from tesla_notifier.runtime import MqttHandler, Scheduler, failure_monitor
from tesla_notifier.runtime.scheduler import parse_cron
from tesla_notifier.storage import PushState, database, push_state


def test_layered_modules_are_importable() -> None:
    """新的分层模块可以被稳定导入。"""
    assert bark is not None
    assert database is not None
    assert failure_monitor is not None
    assert isinstance(push_state, PushState)
    assert MqttHandler is not None
    assert Scheduler is not None
    assert TrafficSampler is not None
    assert get_weather is not None


def test_parse_cron_rejects_invalid_expression() -> None:
    """非法 cron 表达式应被明确拒绝。"""
    try:
        parse_cron("0 8 * *")
    except ValueError:
        return

    raise AssertionError("非法 cron 表达式未触发 ValueError")
