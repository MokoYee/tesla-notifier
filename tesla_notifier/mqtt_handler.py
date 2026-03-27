"""MQTT 订阅模块"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import paho.mqtt.client as mqtt

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("mqtt")

SENTRY_RECORDING_DISPLAY_STATE = 7
AsyncCallback = Callable[[], Coroutine[Any, Any, None]]
AsyncSentryDeactivatedCallback = Callable[
    [float | None, int | None, int],
    Coroutine[Any, Any, None],
]


@dataclass
class VehicleState:
    """车辆状态缓存"""

    state: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    battery_level: int | None = None
    charging_state: str | None = None
    sentry_mode: bool = False
    sentry_activated_at: datetime | None = None
    sentry_activated_battery_level: int | None = None
    sentry_recording_count: int = 0
    center_display_state: int | None = None
    center_display_state_initialized: bool = False
    last_sentry_event_time: datetime | None = None


@dataclass
class MqttHandler:
    """MQTT 消息处理器"""

    car_id: str
    on_trip_end: AsyncCallback | None = None
    on_charging_complete: AsyncCallback | None = None
    on_sentry_activated: AsyncCallback | None = None
    on_sentry_deactivated: AsyncSentryDeactivatedCallback | None = None
    on_sentry_recording: AsyncCallback | None = None

    client: mqtt.Client | None = None
    vehicle_state: VehicleState = field(default_factory=VehicleState)
    _loop: asyncio.AbstractEventLoop | None = None

    def connect(self) -> None:
        """连接 MQTT 服务器"""
        if self.client:
            return

        logger.info(f"正在连接 MQTT 服务器: {config.mqtt_url}")

        callback_api_version = getattr(mqtt, "CallbackAPIVersion", None)
        if callback_api_version is not None:
            self.client = mqtt.Client(
                client_id=f"tesla-notifier-{config.car_id}",
                callback_api_version=callback_api_version.VERSION2,
            )
        else:
            self.client = mqtt.Client(client_id=f"tesla-notifier-{config.car_id}")

        if config.mqtt_username and config.mqtt_password:
            self.client.username_pw_set(config.mqtt_username, config.mqtt_password)
            logger.info("使用认证连接")

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        try:
            self.client.connect(config.mqtt_host, config.mqtt_port, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            logger.exception(f"MQTT 连接失败: {e}")
            self.client = None

    def disconnect(self) -> None:
        """断开 MQTT 连接"""
        if self.client:
            logger.info("正在断开 MQTT 连接...")
            self.client.loop_stop()
            self.client.disconnect()
            self.client = None
            logger.info("MQTT 已断开")

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: Any,
        reason_code: Any,
        properties: Any | None,
    ) -> None:
        """连接成功回调"""
        if reason_code == 0:
            logger.info("MQTT 连接成功")
            topic = f"teslamate/cars/{self.car_id}/#"
            client.subscribe(topic)
            logger.info(f"已订阅主题: {topic}")
        else:
            logger.error(f"MQTT 连接失败: {reason_code}")

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: object,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any | None,
    ) -> None:
        """断开连接回调"""
        logger.warning(f"MQTT 连接断开: {reason_code}")

    def _on_message(
        self, client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage
    ) -> None:
        """消息回调"""
        topic = msg.topic
        payload = msg.payload.decode("utf-8")

        # 提取主题名称
        topic_name = topic.split("/")[-1]
        logger.debug(f"MQTT 收到: {topic_name} = {payload}")

        # 行程结束检测
        if "/state" in topic:
            prev_state = self.vehicle_state.state
            self.vehicle_state.state = payload

            logger.info(f"state 变化: {prev_state} -> {payload}")

            if prev_state == "driving" and payload in ("online", "asleep"):
                logger.info(f"检测到行程结束: {prev_state} -> {payload}")
                if self.on_trip_end and self._loop:
                    logger.info("将在 3 秒后触发行程结束处理")
                    asyncio.run_coroutine_threadsafe(
                        self._delayed_trip_end(), self._loop
                    )

        # 充电状态检测
        if "/charging_state" in topic:
            prev_state = self.vehicle_state.charging_state
            self.vehicle_state.charging_state = payload

            # 记录所有充电状态变化
            logger.info(f"charging_state 变化: {prev_state} -> {payload}")

            if prev_state == "Charging" and payload in (
                "Complete",
                "Stopped",
                "Disconnected",
            ):
                logger.info(f"检测到充电结束: {prev_state} -> {payload}")
                if self.on_charging_complete and self._loop:
                    logger.info("将在 10 秒后触发充电完成处理")
                    asyncio.run_coroutine_threadsafe(
                        self._delayed_charging_complete(), self._loop
                    )

        # 哨兵模式检测
        if "/sentry_mode" in topic:
            prev_sentry = self.vehicle_state.sentry_mode
            current_sentry = payload.lower() == "true"
            self.vehicle_state.sentry_mode = current_sentry

            if not prev_sentry and current_sentry:
                logger.info("========== 哨兵模式已激活 ==========")
                self.vehicle_state.sentry_activated_at = datetime.now()
                self.vehicle_state.sentry_activated_battery_level = (
                    self.vehicle_state.battery_level
                )
                self.vehicle_state.sentry_recording_count = 0
                self.vehicle_state.last_sentry_event_time = None

                if self.on_sentry_activated and self._loop:
                    self._schedule_callback(self.on_sentry_activated)

            elif prev_sentry and not current_sentry:
                logger.info("========== 哨兵模式已关闭 ==========")

                duration_min = None
                battery_drop = None
                recording_count = self.vehicle_state.sentry_recording_count
                if self.vehicle_state.sentry_activated_at:
                    delta = datetime.now() - self.vehicle_state.sentry_activated_at
                    duration_min = delta.total_seconds() / 60
                    self.vehicle_state.sentry_activated_at = None

                start_battery = self.vehicle_state.sentry_activated_battery_level
                end_battery = self.vehicle_state.battery_level
                if start_battery is not None and end_battery is not None:
                    battery_drop = max(start_battery - end_battery, 0)

                self.vehicle_state.sentry_activated_battery_level = None
                self.vehicle_state.sentry_recording_count = 0
                self.vehicle_state.last_sentry_event_time = None

                if self.on_sentry_deactivated and self._loop:
                    self._schedule_sentry_deactivated(
                        duration_min=duration_min,
                        battery_drop=battery_drop,
                        recording_count=recording_count,
                    )

        # 更新车辆状态缓存
        if "/latitude" in topic:
            self.vehicle_state.latitude = float(payload)
        elif "/longitude" in topic:
            self.vehicle_state.longitude = float(payload)
        elif "/center_display_state" in topic:
            self._handle_center_display_state(payload)
        elif "/battery_level" in topic:
            try:
                self.vehicle_state.battery_level = int(payload)
                if (
                    self.vehicle_state.sentry_mode
                    and self.vehicle_state.sentry_activated_battery_level is None
                ):
                    self.vehicle_state.sentry_activated_battery_level = (
                        self.vehicle_state.battery_level
                    )
            except (ValueError, TypeError):
                pass

    def _handle_center_display_state(self, payload: str) -> None:
        """处理 TeslaMate 发布的 center_display_state。

        TeslaMate 前端将 center_display_state == 7 解释为 “Sentry Mode recording”。
        首次收到 retained 快照时只建立基线，不直接触发推送，避免服务重启后误报。
        """
        try:
            current_state = int(payload)
        except (ValueError, TypeError):
            logger.debug(f"解析 center_display_state 失败: {payload}")
            return

        prev_state = self.vehicle_state.center_display_state
        self.vehicle_state.center_display_state = current_state
        logger.debug(f"center_display_state 变化: {prev_state} -> {current_state}")

        if not self.vehicle_state.center_display_state_initialized:
            self.vehicle_state.center_display_state_initialized = True
            logger.debug("收到 center_display_state 初始快照，跳过录制事件检测")
            return

        if (
            prev_state != SENTRY_RECORDING_DISPLAY_STATE
            and current_state == SENTRY_RECORDING_DISPLAY_STATE
        ):
            logger.info("========== 检测到哨兵录制事件（TeslaMate 实时状态） ==========")
            if config.sentry_notify_enabled:
                self._emit_sentry_recording()
            else:
                logger.debug("哨兵录制通知未启用，跳过实时录制推送")

    def _emit_sentry_recording(self) -> None:
        """统一触发哨兵录制推送，复用同一套防抖逻辑。"""
        now = datetime.now()

        if (
            self.vehicle_state.last_sentry_event_time is not None
            and (now - self.vehicle_state.last_sentry_event_time).total_seconds()
            <= config.sentry_recording_cooldown
        ):
            logger.info("哨兵录制事件在防抖窗口内，跳过推送")
            return

        self.vehicle_state.sentry_recording_count += 1

        if self.on_sentry_recording and self._loop:
            self._schedule_callback(self.on_sentry_recording)

        self.vehicle_state.last_sentry_event_time = now

    def _schedule_callback(self, callback: AsyncCallback) -> None:
        """将协程安全切回主事件循环执行。"""
        if not self._loop:
            return

        self._loop.call_soon_threadsafe(self._create_task, callback())

    def _schedule_sentry_deactivated(
        self,
        duration_min: float | None,
        battery_drop: int | None,
        recording_count: int,
    ) -> None:
        """将哨兵关闭事件安全切回主事件循环执行。"""
        if not self._loop or not self.on_sentry_deactivated:
            return

        self._loop.call_soon_threadsafe(
            self._create_task,
            self.on_sentry_deactivated(
                duration_min,
                battery_drop,
                recording_count,
            ),
        )

    @staticmethod
    def _create_task(coro: Coroutine[Any, Any, None]) -> None:
        """统一创建异步任务，便于从线程安全地投递到事件循环。"""
        asyncio.create_task(coro)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """设置事件循环"""
        self._loop = loop

    async def _delayed_trip_end(self) -> None:
        """延迟触发行程结束处理"""
        logger.info("等待 3 秒后触发行程结束处理...")
        await asyncio.sleep(3.0)
        logger.info("延迟结束，开始触发行程结束处理")
        if self.on_trip_end:
            await self.on_trip_end()

    async def _delayed_charging_complete(self) -> None:
        """延迟触发充电完成处理"""
        logger.info("等待 10 秒后触发充电完成处理...")
        await asyncio.sleep(10.0)
        logger.info("延迟结束，开始触发充电完成处理")
        if self.on_charging_complete:
            await self.on_charging_complete()

    async def get_location_str(self) -> str | None:
        """获取当前位置字符串"""
        if not self.vehicle_state.latitude or not self.vehicle_state.longitude:
            return None

        try:
            from tesla_notifier import amap

            address = await amap.reverse_geocode(
                self.vehicle_state.latitude,
                self.vehicle_state.longitude,
            )
            if address:
                return address
        except Exception:
            pass

        return f"{self.vehicle_state.latitude:.4f}, {self.vehicle_state.longitude:.4f}"

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self.client is not None and self.client.is_connected()
