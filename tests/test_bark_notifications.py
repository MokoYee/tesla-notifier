"""Bark 通知展示测试。"""

import asyncio

import pytest

from tesla_notifier.notifications import bark


def test_send_trip_end_merges_scene_and_traffic_and_hides_sampling_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """行程结束通知应合并场景与路况，并隐藏内部采样术语。"""
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
            hard_accel_count=2,
            hard_brake_count=1,
            driving_score=91,
            driving_label="稳健",
            road_context="城市通勤",
            trip_commentary="几百公里一口气拿下，牛逼啊🐮",
            traffic_label="整体畅通",
            traffic_summary="整体畅通, 采样5次, 压力指数4.2",
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
    commentary_index = lines.index("🧠 行程点评 · 几百公里一口气拿下，牛逼啊🐮")
    actions_index = lines.index("📌 驾驶动作 · 急加速2次 · 急减速1次")
    scene_index = lines.index("🧭 行驶场景 · 城市通勤 · 整体畅通")

    assert score_index < commentary_index < actions_index < scene_index
    assert "采样" not in body
    assert "压力指数" not in body
    assert "🚦 沿途路况" not in body


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
