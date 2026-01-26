"""MQTT 订阅模块"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

import paho.mqtt.client as mqtt

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("mqtt")


@dataclass
class VehicleState:
    """车辆状态缓存"""

    state: str | None = None  # online/asleep/driving/charging
    shift_state: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    battery_level: int | None = None
    rated_range: float | None = None
    plugged_in: bool = False
    charging_state: str | None = None
    sentry_mode: bool = False
    sentry_activated_at: datetime | None = None


@dataclass
class MqttHandler:
    """MQTT 消息处理器"""

    car_id: str
    on_trip_end: asyncio.coroutines = None  # type: ignore[type-arg]
    on_charging_complete: asyncio.coroutines = None  # type: ignore[type-arg]
    on_sentry_activated: asyncio.coroutines = None  # type: ignore[type-arg]
    on_sentry_deactivated: asyncio.coroutines = None  # type: ignore[type-arg]

    client: mqtt.Client | None = None
    vehicle_state: VehicleState = field(default_factory=VehicleState)
    pushed_trips: set[int] = field(default_factory=set)
    pushed_charges: set[int] = field(default_factory=set)
    _loop: asyncio.AbstractEventLoop | None = None

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

        # 行程结束检测：state 从 driving 变为 online/asleep（TeslaMate 已确认行程结束）
        if "/state" in topic:
            prev_state = self.vehicle_state.state
            self.vehicle_state.state = payload

            logger.debug(f"state 变化: {prev_state} -> {payload}")

            # 检测行程结束：driving -> online/asleep
            if prev_state == "driving" and payload in ("online", "asleep"):
                logger.info(
                    f"检测到行程结束（TeslaMate 已确认）: {prev_state} -> {payload}"
                )
                if self.on_trip_end and self._loop:
                    logger.info("将在 3 秒后触发行程结束处理")
                    asyncio.run_coroutine_threadsafe(
                        self._delayed_trip_end_short(), self._loop
                    )

        # 保留 shift_state 监听用于调试（不再触发推送）
        if "/shift_state" in topic:
            prev_state = self.vehicle_state.shift_state
            self.vehicle_state.shift_state = payload

            logger.debug(f"shift_state 变化: {prev_state} -> {payload}")
            # 不再基于 shift_state 触发推送，仅用于调试

        # 充电状态检测
        if "/charging_state" in topic:
            prev_state = self.vehicle_state.charging_state
            self.vehicle_state.charging_state = payload

            if prev_state == "Charging" and payload in ("Complete", "Stopped"):
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

            # 哨兵模式激活
            if not prev_sentry and current_sentry:
                logger.info("========== 哨兵模式已激活 ==========")
                self.vehicle_state.sentry_activated_at = datetime.now()
                if self.on_sentry_activated and self._loop:
                    self._loop.call_soon(
                        lambda: asyncio.create_task(self.on_sentry_activated()),
                    )

            # 哨兵模式关闭
            elif prev_sentry and not current_sentry:
                logger.info("========== 哨兵模式已关闭 ==========")
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
            self.vehicle_state.battery_level = int(payload)
        elif "/rated_battery_range_km" in topic:
            self.vehicle_state.rated_range = float(payload)
        elif "/plugged_in" in topic:
            self.vehicle_state.plugged_in = payload.lower() == "true"

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """设置事件循环"""
        self._loop = loop

    async def _delayed_trip_end_short(self) -> None:
        """延迟触发行程结束处理（短延迟，因为 TeslaMate 已确认）"""
        logger.info("等待 3 秒后触发行程结束处理...")
        await asyncio.sleep(3.0)
        logger.info("延迟结束，开始触发行程结束处理")
        if self.on_trip_end:
            await self.on_trip_end()

    async def _delayed_trip_end(self) -> None:
        """延迟触发行程结束处理（已废弃，保留用于兼容）"""
        logger.warning("使用了已废弃的 _delayed_trip_end 方法")
        await self._delayed_trip_end_short()

    async def _delayed_charging_complete(self) -> None:
        """延迟触发充电完成处理"""
        logger.info("等待 10 秒后触发充电完成处理...")
        await asyncio.sleep(10.0)
        logger.info("延迟结束，开始触发充电完成处理")
        if self.on_charging_complete:
            await self.on_charging_complete()

    async def get_location_str(self) -> str | None:
        """获取当前位置字符串（优先返回地址，失败则返回坐标）"""
        if not self.vehicle_state.latitude or not self.vehicle_state.longitude:
            return None

        # 尝试通过高德地图获取地址
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

        # 降级：返回坐标
        return f"{self.vehicle_state.latitude:.4f}, {self.vehicle_state.longitude:.4f}"

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self.client is not None and self.client.is_connected()
