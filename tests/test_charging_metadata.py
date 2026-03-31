"""充电元数据测试。"""

from tesla_notifier.notifications.bark import _format_charging_type_label
from tesla_notifier.storage.database import _calculate_charging_efficiency


def test_calculate_charging_efficiency_uses_greater_energy_value() -> None:
    """充电效率应与当前项目的统一统计口径保持一致。"""
    efficiency = _calculate_charging_efficiency(42.8, 46.1)

    assert efficiency is not None
    assert round(efficiency) == 93


def test_calculate_charging_efficiency_returns_full_when_used_is_missing() -> None:
    """缺少总取电量时，应退化为 100% 而不是报错。"""
    efficiency = _calculate_charging_efficiency(30.2, None)

    assert efficiency == 100.0


def test_format_charging_type_label() -> None:
    """充电类型标签应符合通知展示文案。"""
    assert _format_charging_type_label("DC") == "DC 快充"
    assert _format_charging_type_label("AC") == "AC 慢充"
    assert _format_charging_type_label(None) is None
