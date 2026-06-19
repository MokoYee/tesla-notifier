"""MQTT 数据新鲜度监控。"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger
from tesla_notifier.runtime.health import SystemAlert

logger = setup_logger("mqtt_freshness")

AsyncAlertHandler = Callable[[SystemAlert], Awaitable[None]]
LatestPositionTimeProvider = Callable[[str], Awaitable[datetime | None]]


@dataclass(frozen=True)
class MqttFreshnessSnapshot:
    """MQTT 与数据库时间戳快照。"""

    checked_at: datetime
    db_latest_position_at: datetime | None
    mqtt_since_at: datetime | None
    mqtt_last_message_at: datetime | None


@dataclass
class MqttFreshnessMonitor:
    """比较 TeslaMate 数据库写入与 MQTT 实时消息的新鲜度。"""

    car_id: str
    latest_position_time_provider: LatestPositionTimeProvider
    alert_handler: AsyncAlertHandler
    stale_after_seconds: int
    db_active_window_seconds: int
    _alert_open: bool = False
    _last_alert_snapshot: MqttFreshnessSnapshot | None = None
    _last_mqtt_message_at: datetime | None = None
    _last_mqtt_since_at: datetime | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def record_mqtt_message(
        self,
        *,
        received_at: datetime | None = None,
        since_at: datetime | None = None,
    ) -> None:
        """记录最近收到的 MQTT 消息时间。"""
        now = _ensure_utc(received_at or datetime.now(UTC))
        self._last_mqtt_message_at = now
        if since_at is not None:
            self._last_mqtt_since_at = _ensure_utc(since_at)

    async def check_once(self) -> MqttFreshnessSnapshot:
        """执行一次新鲜度检查，并在状态变化时发送系统告警。"""
        async with self._lock:
            checked_at = datetime.now(UTC)
            db_latest_position_at = _ensure_utc(
                await self.latest_position_time_provider(self.car_id)
            )
            snapshot = MqttFreshnessSnapshot(
                checked_at=checked_at,
                db_latest_position_at=db_latest_position_at,
                mqtt_since_at=self._last_mqtt_since_at,
                mqtt_last_message_at=self._last_mqtt_message_at,
            )

            stale = self._is_stale(snapshot)
            if stale and not self._alert_open:
                self._alert_open = True
                self._last_alert_snapshot = snapshot
                await self.alert_handler(self._build_alert(snapshot))
            elif self._alert_open and self._is_recovered(snapshot):
                self._alert_open = False
                await self.alert_handler(self._build_recovery(snapshot))
                self._last_alert_snapshot = None

            return snapshot

    def _is_stale(self, snapshot: MqttFreshnessSnapshot) -> bool:
        """判断 MQTT 是否相对数据库明显停滞。"""
        if snapshot.db_latest_position_at is None:
            return False

        db_age = (snapshot.checked_at - snapshot.db_latest_position_at).total_seconds()
        if db_age > self.db_active_window_seconds:
            return False

        if snapshot.mqtt_last_message_at is not None:
            mqtt_message_age = (
                snapshot.checked_at - snapshot.mqtt_last_message_at
            ).total_seconds()
            if mqtt_message_age <= self.stale_after_seconds:
                return False

        if snapshot.mqtt_since_at is None:
            return True

        lag_seconds = (
            snapshot.db_latest_position_at - snapshot.mqtt_since_at
        ).total_seconds()
        return lag_seconds >= self.stale_after_seconds

    def _is_recovered(self, snapshot: MqttFreshnessSnapshot) -> bool:
        """判断已打开的停滞告警是否恢复。"""
        if snapshot.mqtt_last_message_at is not None:
            mqtt_message_age = (
                snapshot.checked_at - snapshot.mqtt_last_message_at
            ).total_seconds()
            if mqtt_message_age <= self.stale_after_seconds:
                return True

        if snapshot.db_latest_position_at is None or snapshot.mqtt_since_at is None:
            return False

        lag_seconds = (
            snapshot.db_latest_position_at - snapshot.mqtt_since_at
        ).total_seconds()
        return lag_seconds < self.stale_after_seconds

    def _build_alert(self, snapshot: MqttFreshnessSnapshot) -> SystemAlert:
        """构造 MQTT 停滞告警。"""
        details = [
            f"数据库最新位置 {_format_utc(snapshot.db_latest_position_at)}",
            f"MQTT since {_format_utc(snapshot.mqtt_since_at)}",
            f"最近 MQTT 消息 {_format_utc(snapshot.mqtt_last_message_at)}",
            f"判定阈值 {self.stale_after_seconds} 秒",
        ]

        return SystemAlert(
            component="mqtt",
            status="alert",
            severity="high",
            summary="MQTT 实时数据停滞",
            reason="数据库仍有新位置写入，但 MQTT 实时状态长时间未更新",
            details=tuple(details),
            event_key="mqtt-freshness-stale",
        )

    def _build_recovery(self, snapshot: MqttFreshnessSnapshot) -> SystemAlert:
        """构造 MQTT 停滞恢复告警。"""
        details = [
            f"数据库最新位置 {_format_utc(snapshot.db_latest_position_at)}",
            f"MQTT since {_format_utc(snapshot.mqtt_since_at)}",
            f"最近 MQTT 消息 {_format_utc(snapshot.mqtt_last_message_at)}",
        ]

        if self._last_alert_snapshot is not None:
            duration = int(
                (
                    snapshot.checked_at - self._last_alert_snapshot.checked_at
                ).total_seconds()
            )
            details.append(f"停滞持续约 {duration} 秒")

        return SystemAlert(
            component="mqtt",
            status="recovered",
            severity="medium",
            summary="MQTT 实时数据已恢复",
            reason="MQTT 实时状态重新更新，已追上数据库写入",
            details=tuple(details),
            event_key="mqtt-freshness-recovered",
        )


def parse_teslamate_since(payload: str) -> datetime | None:
    """解析 TeslaMate MQTT since 时间。"""
    value = payload.strip()
    if not value or value.lower() == "nil":
        return None

    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        logger.debug(f"解析 MQTT since 失败: {payload}")
        return None

    return _ensure_utc(parsed)


async def run_mqtt_freshness_monitor(
    monitor: MqttFreshnessMonitor,
) -> None:
    """周期执行 MQTT 新鲜度检查。"""
    logger.info("MQTT 数据新鲜度监控已启动")

    try:
        await asyncio.sleep(float(config.mqtt_freshness_check_interval))
        while True:
            try:
                await monitor.check_once()
            except Exception as e:
                logger.exception(f"MQTT 数据新鲜度检查异常: {e}")

            await asyncio.sleep(float(config.mqtt_freshness_check_interval))
    except asyncio.CancelledError:
        logger.info("MQTT 数据新鲜度监控已停止")
        raise


def _ensure_utc(value: datetime | None) -> datetime | None:
    """将数据库或 MQTT 时间归一化为 UTC aware datetime。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_utc(value: datetime | None) -> str:
    """格式化 UTC 时间，空值使用中文占位。"""
    if value is None:
        return "未知"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
