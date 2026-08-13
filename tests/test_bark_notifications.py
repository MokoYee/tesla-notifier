"""Bark 通知展示测试。"""

import asyncio

import pytest

from tesla_notifier.notifications import bark


def test_send_trip_end_compacts_analysis_into_three_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """行程分析只保留评分、点评和关键因素，避免重复展示。"""
    captured: dict[str, str] = {}

    async def fake_send_notification(options: bark.BarkOptions) -> bool:
        captured["body"] = options.body
        return True

    monkeypatch.setattr(bark, "send_notification", fake_send_notification)

    sent = asyncio.run(
        bark.send_trip_end(
            start_address="上海市徐汇区",
            end_address="上海市浦东新区",
            start_time="2026-03-31T08:00:00+08:00",
            end_time="2026-03-31T09:00:00+08:00",
            distance=42.6,
            duration=60.0,
            energy_used=6.8,
            efficiency=160.0,
            start_range=420.0,
            end_range=372.0,
            start_soc=78,
            end_soc=69,
            outside_temp=21.5,
            driving_score=91,
            driving_label="稳健",
            trip_commentary="城市通勤为主，节奏正常",
            key_factors=["城市低速 82%", "急加速 2 次、急减速 1 次"],
            speed_avg=43.2,
            speed_max=86.0,
            odometer=12345.6,
            trip_id=9527,
        )
    )

    assert sent is True
    body = captured["body"]
    lines = body.splitlines()

    score_index = lines.index("🏁 评分参考 91 分 · 稳健")
    commentary_index = lines.index("🧠 行程点评 · 城市通勤为主，节奏正常")
    factors_index = lines.index("📌 关键因素 · 城市低速 82% · 急加速 2 次、急减速 1 次")

    assert score_index < commentary_index < factors_index
    assert "🔎 路况结构" not in body
    assert "🔎 能耗解释" not in body
    assert "🔎 驾驶画像" not in body
    assert "📌 驾驶动作" not in body
    assert "🧭 行驶场景" not in body


def test_send_sentry_recording_includes_rated_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """哨兵录制通知应与开启/关闭一致，展示电量和表显续航。"""
    captured: dict[str, str] = {}

    async def fake_send_notification(options: bark.BarkOptions) -> bool:
        captured["body"] = options.body
        return True

    monkeypatch.setattr(bark, "send_notification", fake_send_notification)

    sent = asyncio.run(
        bark.send_sentry_recording(
            location="上海市闵行区",
            battery_level=81,
            rated_range_km=463.0,
            recording_count=2,
            session_tag="20260331093000",
        )
    )

    assert sent is True
    assert "🔋 电量 81% · 463 km" in captured["body"]


def test_send_charging_issue_alert_marks_no_power_as_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NoPower 应被视为直接状态信号，而不是规则推断。"""
    captured: dict[str, object] = {}

    async def fake_send_notification(options: bark.BarkOptions) -> bool:
        captured["title"] = options.title
        captured["subtitle"] = options.subtitle
        captured["body"] = options.body
        captured["meta"] = options.meta
        return True

    monkeypatch.setattr(bark, "send_notification", fake_send_notification)

    sent = asyncio.run(
        bark.send_charging_issue_alert(
            issue_type="no_power",
            location="上海市闵行区",
            battery_level=46,
            charge_limit_soc=80,
            charger_power=0.0,
            plugged_in=True,
            session_tag="20260331153000",
        )
    )

    assert sent is True
    assert captured["title"] == "⚡ 充电电源异常"
    assert captured["subtitle"] == "当前无供电"
    assert "车辆已连接，但当前未获取到供电" in str(captured["body"])
    assert "建议优先检查供电是否正常，再确认充电枪和桩端连接" in str(captured["body"])
    meta = captured["meta"]
    assert isinstance(meta, bark.NotificationMeta)
    assert meta.certainty == "fact"
    assert meta.reason == "TeslaMate MQTT charging_state = NoPower"


def test_send_charging_issue_alert_marks_stopped_early_as_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopped 未达目标应按规则推断展示，避免包装成明确故障。"""
    captured: dict[str, object] = {}

    async def fake_send_notification(options: bark.BarkOptions) -> bool:
        captured["title"] = options.title
        captured["subtitle"] = options.subtitle
        captured["body"] = options.body
        captured["meta"] = options.meta
        return True

    monkeypatch.setattr(bark, "send_notification", fake_send_notification)

    sent = asyncio.run(
        bark.send_charging_issue_alert(
            issue_type="stopped_early",
            location="上海市闵行区",
            battery_level=57,
            charge_limit_soc=80,
            charger_power=0.0,
            plugged_in=True,
            session_tag="20260331153100",
        )
    )

    assert sent is True
    assert captured["title"] == "⚠️ 充电提前停止"
    assert captured["subtitle"] == "仍低于设定上限"
    assert "当前电量未达到设定上限，充电已提前停止" in str(captured["body"])
    assert "建议查看充电桩会话或车辆状态，确认后可重新插枪继续充电" in str(
        captured["body"]
    )
    meta = captured["meta"]
    assert isinstance(meta, bark.NotificationMeta)
    assert meta.certainty == "analysis"
    assert "charging_state = Stopped" in (meta.reason or "")
