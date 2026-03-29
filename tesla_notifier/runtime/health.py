"""系统健康监控模块。"""

import asyncio
import contextlib
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("health")

AlertStatus = Literal["alert", "recovered"]
AlertSeverity = Literal["high", "medium", "low"]
AsyncAlertHandler = Callable[["SystemAlert"], Coroutine[Any, Any, None]]


@dataclass(frozen=True)
class SystemAlert:
    """系统告警事件。"""

    component: str
    status: AlertStatus
    severity: AlertSeverity
    summary: str
    reason: str
    details: tuple[str, ...] = ()
    event_key: str = ""


@dataclass
class FailureMonitor:
    """轻量故障监控器。"""

    _loop: asyncio.AbstractEventLoop | None = None
    _alert_handler: AsyncAlertHandler | None = None
    _watchdog_task: asyncio.Task[None] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _db_consecutive_failures: int = 0
    _db_alert_open: bool = False
    _last_db_error: str | None = None
    _mqtt_disconnected_at: datetime | None = None
    _mqtt_disconnect_reason: str | None = None
    _mqtt_alert_open: bool = False
    _bark_consecutive_failures: int = 0

    def configure(
        self,
        loop: asyncio.AbstractEventLoop,
        alert_handler: AsyncAlertHandler,
    ) -> None:
        """绑定事件循环和异步告警处理器。"""
        self._loop = loop
        self._alert_handler = alert_handler

    def start(self) -> None:
        """启动后台健康监控任务。"""
        if self._loop is None or self._watchdog_task is not None:
            return
        self._watchdog_task = self._loop.create_task(self._mqtt_watchdog())

    async def shutdown(self) -> None:
        """停止后台任务。"""
        if self._watchdog_task is None:
            return

        self._watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._watchdog_task
        self._watchdog_task = None

    def record_db_success(self) -> None:
        """记录数据库访问成功。"""
        recovery_alert: SystemAlert | None = None

        with self._lock:
            had_alert = self._db_alert_open
            failure_count = self._db_consecutive_failures
            last_error = self._last_db_error

            self._db_consecutive_failures = 0
            self._db_alert_open = False
            self._last_db_error = None

        if had_alert:
            details = [f"恢复前连续失败 {failure_count} 次"]
            if last_error:
                details.append(f"最近错误 {last_error}")
            recovery_alert = SystemAlert(
                component="database",
                status="recovered",
                severity="medium",
                summary="数据库链路已恢复",
                reason="数据库访问恢复正常",
                details=tuple(details),
                event_key="database-recovered",
            )

        if recovery_alert is not None:
            self._dispatch_alert(recovery_alert)

    def record_db_failure(self, detail: str) -> None:
        """记录数据库访问失败。"""
        alert: SystemAlert | None = None

        with self._lock:
            self._db_consecutive_failures += 1
            self._last_db_error = detail

            if (
                not self._db_alert_open
                and self._db_consecutive_failures >= config.db_failure_alert_threshold
            ):
                self._db_alert_open = True
                alert = SystemAlert(
                    component="database",
                    status="alert",
                    severity="high",
                    summary="数据库链路异常",
                    reason="数据库访问连续失败，已超过告警阈值",
                    details=(
                        f"连续失败 {self._db_consecutive_failures} 次",
                        f"最近错误 {detail}",
                    ),
                    event_key="database-failure",
                )

        logger.warning(f"数据库失败计数更新: {self._db_consecutive_failures}")
        if alert is not None:
            self._dispatch_alert(alert)

    def record_mqtt_connected(self) -> None:
        """记录 MQTT 已恢复连接。"""
        recovery_alert: SystemAlert | None = None

        with self._lock:
            disconnected_at = self._mqtt_disconnected_at
            disconnect_reason = self._mqtt_disconnect_reason
            had_alert = self._mqtt_alert_open

            self._mqtt_disconnected_at = None
            self._mqtt_disconnect_reason = None
            self._mqtt_alert_open = False

        if had_alert and disconnected_at is not None:
            duration = int((datetime.now() - disconnected_at).total_seconds())
            details = [f"中断持续 {duration} 秒"]
            if disconnect_reason:
                details.append(f"最近原因 {disconnect_reason}")
            recovery_alert = SystemAlert(
                component="mqtt",
                status="recovered",
                severity="medium",
                summary="MQTT 实时链路已恢复",
                reason="MQTT 已重新建立连接",
                details=tuple(details),
                event_key="mqtt-recovered",
            )

        if recovery_alert is not None:
            self._dispatch_alert(recovery_alert)

    def record_mqtt_disconnected(self, detail: str) -> None:
        """记录 MQTT 断开。"""
        with self._lock:
            if self._mqtt_disconnected_at is None:
                self._mqtt_disconnected_at = datetime.now()
            self._mqtt_disconnect_reason = detail

        logger.warning(f"MQTT 断链记录: {detail}")

    def record_bark_success(self) -> None:
        """记录 Bark 推送成功。"""
        self._bark_consecutive_failures = 0

    def record_bark_failure(self, detail: str) -> None:
        """记录 Bark 推送失败。

        Bark 自身失败时无法再通过 Bark 发送实时告警，这里只保留日志与计数，
        避免因为自引用告警造成递归失败。
        """
        self._bark_consecutive_failures += 1
        logger.warning(
            "Bark 推送失败计数=%s, detail=%s",
            self._bark_consecutive_failures,
            detail,
        )

    async def _mqtt_watchdog(self) -> None:
        """周期检查 MQTT 是否持续断链。"""
        while True:
            await asyncio.sleep(15)
            alert: SystemAlert | None = None

            with self._lock:
                disconnected_at = self._mqtt_disconnected_at
                disconnect_reason = self._mqtt_disconnect_reason
                alert_open = self._mqtt_alert_open

                if disconnected_at is None or alert_open:
                    continue

                duration = int((datetime.now() - disconnected_at).total_seconds())
                if duration < config.mqtt_disconnect_alert_after:
                    continue

                self._mqtt_alert_open = True
                alert = SystemAlert(
                    component="mqtt",
                    status="alert",
                    severity="high",
                    summary="MQTT 实时链路中断",
                    reason="MQTT 持续断开，实时事件可能暂时无法监听",
                    details=(
                        f"持续中断 {duration} 秒",
                        f"最近原因 {disconnect_reason or '未知'}",
                    ),
                    event_key="mqtt-disconnect",
                )

            if alert is not None:
                self._dispatch_alert(alert)

    def _dispatch_alert(self, alert: SystemAlert) -> None:
        """将告警投递到主事件循环。"""
        if (
            not config.failure_alert_notify_enabled
            or self._loop is None
            or self._alert_handler is None
        ):
            return

        self._loop.call_soon_threadsafe(
            self._create_task,
            self._alert_handler(alert),
        )

    @staticmethod
    def _create_task(coro: Coroutine[Any, Any, None]) -> None:
        """在线程安全场景下统一创建任务。"""
        asyncio.create_task(coro)


failure_monitor = FailureMonitor()
