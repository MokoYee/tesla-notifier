"""MQTT 数据新鲜度监控测试。"""

import asyncio
from datetime import UTC, datetime, timedelta

from tesla_notifier.runtime.health import SystemAlert
from tesla_notifier.runtime.mqtt_freshness import (
    MqttFreshnessMonitor,
    parse_teslamate_since,
)


def test_parse_teslamate_since_supports_z_suffix() -> None:
    """TeslaMate 的 Z 后缀时间应被解析为 UTC 时间。"""
    parsed = parse_teslamate_since("2026-06-19T13:06:35.092455Z")

    assert parsed == datetime(2026, 6, 19, 13, 6, 35, 92455, tzinfo=UTC)


def test_mqtt_freshness_alerts_and_recovers() -> None:
    """数据库持续写入但 MQTT 停滞时告警，MQTT 恢复后发送恢复。"""
    alerts: list[SystemAlert] = []
    base_time = datetime.now(UTC)
    latest_position_at = base_time - timedelta(minutes=2)

    async def latest_position_provider(_car_id: str) -> datetime:
        return latest_position_at

    async def alert_handler(alert: SystemAlert) -> None:
        alerts.append(alert)

    monitor = MqttFreshnessMonitor(
        car_id="1",
        latest_position_time_provider=latest_position_provider,
        alert_handler=alert_handler,
        stale_after_seconds=300,
        db_active_window_seconds=1800,
    )
    monitor.record_mqtt_message(
        received_at=base_time - timedelta(minutes=20),
        since_at=base_time - timedelta(minutes=20),
    )

    asyncio.run(monitor.check_once())

    assert len(alerts) == 1
    assert alerts[0].status == "alert"
    assert alerts[0].event_key == "mqtt-freshness-stale"

    monitor.record_mqtt_message(
        received_at=datetime.now(UTC),
        since_at=datetime.now(UTC),
    )
    asyncio.run(monitor.check_once())

    assert len(alerts) == 2
    assert alerts[1].status == "recovered"
    assert alerts[1].event_key == "mqtt-freshness-recovered"


def test_mqtt_freshness_does_not_alert_when_database_is_inactive() -> None:
    """数据库自身也长时间无新位置时，不应误判 MQTT 停滞。"""
    alerts: list[SystemAlert] = []
    base_time = datetime.now(UTC)

    async def latest_position_provider(_car_id: str) -> datetime:
        return base_time - timedelta(hours=2)

    async def alert_handler(alert: SystemAlert) -> None:
        alerts.append(alert)

    monitor = MqttFreshnessMonitor(
        car_id="1",
        latest_position_time_provider=latest_position_provider,
        alert_handler=alert_handler,
        stale_after_seconds=300,
        db_active_window_seconds=1800,
    )
    monitor.record_mqtt_message(
        received_at=base_time - timedelta(hours=2),
        since_at=base_time - timedelta(hours=2),
    )

    asyncio.run(monitor.check_once())

    assert alerts == []
