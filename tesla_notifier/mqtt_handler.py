"""MQTT 订阅模块"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("mqtt")


@dataclass
class VehicleState:
    """车辆状态缓存"""

    state: str | None = None
    shift_state: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    battery_level: int | None = None
    rated_range: float | None = None
    plugged_in: bool = False
    charging_state: str | None = None
    sentry_mode: bool = False
    sentry_activated_at: datetime | None = None
    battery_history: deque = field(default_factory=lambda: deque(maxlen=20))
    last_sentry_event_time: datetime | None = None
    power: float = 0.0


@dataclass
class MqttHandler:
    """MQTT 消息处理器"""

    car_id: str
    on_trip_end: asyncio.coroutines = None  # type: ignore[type-arg]
    on_charging_complete: asyncio.coroutines = None  # type: ignore[type-arg]
    on_sentry_activated: asyncio.coroutines = None  # type: ignore[type-arg]
    on_sentry_deactivated: asyncio.coroutines = None  # type: ignore[type-arg]
    on_sentry_recording: asyncio.coroutines = None  # type: ignore[type-arg]

    client: mqtt.Client | None = None
    vehicle_state: VehicleState = field(default_factory=VehicleState)
    pushed_trips: set[int] = field(default_factory=set)
    pushed_charges: set[int] = field(default_factory=set)
    _loop: asyncio.AbstractEventLoop | None = None
    _battery_monitor_task: asyncio.Task | None = None

    def connect(self) -> None:
        """连接 MQTT 服务器"""
        if self.client:
            return

        logger.info(f"正在连接 MQTT 服务器: {config.mqtt_url}")

        self.client = mqtt.Client(
            client_id=f"tesla-notifier-{config.car_id}",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )

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
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
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
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
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

        if "/shift_state" in topic:
            prev_state = self.vehicle_state.shift_state
            self.vehicle_state.shift_state = payload
            logger.debug(f"shift_state 变化: {prev_state} -> {payload}")

        # 充电状态检测
        if "/charging_state" in topic:
            prev_state = self.vehicle_state.charging_state
            self.vehicle_state.charging_state = payload

            # 记录所有充电状态变化
            logger.info(f"charging_state 变化: {prev_state} -> {payload}")

            if prev_state == "Charging" and payload in ("Complete", "Stopped", "Disconnected"):
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
                logger.info(
                    f"哨兵录制检测: {'已启用' if config.sentry_notify_enabled else '已禁用'}, "
                    f"电池下降阈值: {config.sentry_battery_drop_threshold:.3f}%/min, "
                    f"防抖间隔: {config.sentry_recording_cooldown}s"
                )
                self.vehicle_state.sentry_activated_at = datetime.now()

                if config.sentry_notify_enabled and self._loop and not self._battery_monitor_task:
                    logger.info("启动电池下降速率监控任务...")
                    self._battery_monitor_task = self._loop.create_task(self.start_battery_monitor())

                if self.on_sentry_activated and self._loop:
                    self._loop.call_soon(
                        lambda: asyncio.create_task(self.on_sentry_activated()),
                    )

            elif prev_sentry and not current_sentry:
                logger.info("========== 哨兵模式已关闭 ==========")

                if self._battery_monitor_task:
                    logger.info("停止电池监控任务...")
                    self._battery_monitor_task.cancel()
                    self._battery_monitor_task = None

                duration_min = None
                if self.vehicle_state.sentry_activated_at:
                    delta = datetime.now() - self.vehicle_state.sentry_activated_at
                    duration_min = delta.total_seconds() / 60
                    self.vehicle_state.sentry_activated_at = None

                if self.on_sentry_deactivated and self._loop:
                    self._loop.call_soon(
                        lambda d=duration_min: asyncio.create_task(
                            self.on_sentry_deactivated(d)
                        ),
                    )

        # 更新车辆状态缓存
        if "/latitude" in topic:
            self.vehicle_state.latitude = float(payload)
        elif "/longitude" in topic:
            self.vehicle_state.longitude = float(payload)
        elif "/battery_level" in topic:
            try:
                current_soc = int(payload)
                self.vehicle_state.battery_level = current_soc

                now = datetime.now()
                if self.vehicle_state.battery_history:
                    last_time, last_soc = self.vehicle_state.battery_history[-1]
                    if current_soc != last_soc or (now - last_time).total_seconds() > 60:
                        self.vehicle_state.battery_history.append((now, current_soc))
                else:
                    self.vehicle_state.battery_history.append((now, current_soc))
            except (ValueError, TypeError):
                pass
        elif "/rated_battery_range_km" in topic:
            self.vehicle_state.rated_range = float(payload)
        elif "/plugged_in" in topic:
            self.vehicle_state.plugged_in = payload.lower() == "true"
        elif "/power" in topic:
            try:
                power_kw = float(payload)
                power_w = abs(power_kw * 1000)
                self.vehicle_state.power = power_w

                if self.vehicle_state.sentry_mode:
                    logger.debug(f"[哨兵功率] {power_w:.0f}W")
            except (ValueError, TypeError) as e:
                logger.debug(f"解析功率数据失败: {payload}, 错误: {e}")

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """设置事件循环"""
        self._loop = loop

    async def start_battery_monitor(self) -> None:
        """启动电池下降速率监控任务（用于哨兵录制检测）"""
        try:
            while True:
                await asyncio.sleep(45)

                if not self.vehicle_state.sentry_mode:
                    continue

                if self.vehicle_state.plugged_in:
                    continue

                if self.vehicle_state.state != "online":
                    continue

                now = datetime.now()

                while (
                    self.vehicle_state.battery_history
                    and self.vehicle_state.battery_history[0][0] < now - timedelta(minutes=10)
                ):
                    self.vehicle_state.battery_history.popleft()

                if len(self.vehicle_state.battery_history) < 5:
                    continue

                oldest_time, oldest_soc = self.vehicle_state.battery_history[0]
                newest_time, newest_soc = self.vehicle_state.battery_history[-1]
                time_diff_min = (newest_time - oldest_time).total_seconds() / 60

                if time_diff_min < 3:
                    continue

                soc_drop = oldest_soc - newest_soc
                drop_per_min = soc_drop / time_diff_min
                threshold = config.sentry_battery_drop_threshold

                if drop_per_min > threshold:
                    if (
                        self.vehicle_state.last_sentry_event_time is None
                        or (now - self.vehicle_state.last_sentry_event_time).total_seconds()
                        > config.sentry_recording_cooldown
                    ):
                        logger.info(
                            f"========== 检测到疑似哨兵录制事件 ==========\n"
                            f"电池下降速率: {drop_per_min:.3f}%/min (阈值: {threshold:.3f}%/min)\n"
                            f"时间窗口: {time_diff_min:.1f} 分钟\n"
                            f"电量变化: {oldest_soc}% -> {newest_soc}% (下降 {soc_drop}%)"
                        )

                        if self.on_sentry_recording and self._loop:
                            self._loop.call_soon(
                                lambda: asyncio.create_task(self.on_sentry_recording(drop_per_min)),
                            )

                        self.vehicle_state.last_sentry_event_time = now

        except asyncio.CancelledError:
            logger.info("电池监控任务已取消")
        except Exception as e:
            logger.exception(f"电池监控任务异常: {e}")

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
                self.vehicle_state.longitude
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
