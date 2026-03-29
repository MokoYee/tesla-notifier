"""MQTT 订阅模块"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import paho.mqtt.client as mqtt

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger
from tesla_notifier.runtime.health import failure_monitor

logger = setup_logger("mqtt")

SENTRY_RECORDING_DISPLAY_STATE = 7
CHARGING_STOP_CONFIRM_DELAY_SEC = 60.0
TIRE_LABELS = {
    "fl": "左前",
    "fr": "右前",
    "rl": "左后",
    "rr": "右后",
}

AsyncCallback = Callable[[], Coroutine[Any, Any, None]]
AsyncSentryDeactivatedCallback = Callable[
    [float | None, int | None, float | None, float | None, int],
    Coroutine[Any, Any, None],
]
AsyncPositionCallback = Callable[
    [float | None, float | None],
    Coroutine[Any, Any, None],
]


@dataclass
class VehicleState:
    """车辆状态缓存"""

    state: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    geofence: str | None = None
    battery_level: int | None = None
    rated_range_km: float | None = None
    charging_state: str | None = None
    charge_enable_request: bool | None = None
    charge_energy_added: float | None = None
    charge_limit_soc: int | None = None
    charger_power: float | None = None
    plugged_in: bool | None = None
    charge_port_door_open: bool | None = None
    locked: bool | None = None
    is_user_present: bool | None = None
    windows_open: bool | None = None
    doors_open: bool | None = None
    trunk_open: bool | None = None
    frunk_open: bool | None = None
    sentry_mode: bool = False
    sentry_activated_at: datetime | None = None
    sentry_activated_battery_level: int | None = None
    sentry_activated_rated_range_km: float | None = None
    sentry_recording_count: int = 0
    center_display_state: int | None = None
    center_display_state_initialized: bool = False
    last_sentry_event_time: datetime | None = None
    tpms_pressure_fl: float | None = None
    tpms_pressure_fr: float | None = None
    tpms_pressure_rl: float | None = None
    tpms_pressure_rr: float | None = None
    tpms_soft_warning_fl: bool | None = None
    tpms_soft_warning_fr: bool | None = None
    tpms_soft_warning_rl: bool | None = None
    tpms_soft_warning_rr: bool | None = None
    initialized_topics: set[str] = field(default_factory=set)
    departure_check_session_id: int = 0
    departure_alert_sent: bool = False
    last_tire_alert_time: datetime | None = None
    pending_charging_issue: bool = False
    active_charging_issue: str | None = None
    last_charging_issue_key: str | None = None
    last_charging_issue_time: datetime | None = None
    charging_state_version: int = 0
    charging_session_had_active_charge: bool = False
    charging_stop_pending_confirmation: bool = False
    drive_state_version: int = 0


@dataclass
class MqttHandler:
    """MQTT 消息处理器"""

    car_id: str
    on_trip_started: AsyncCallback | None = None
    on_trip_stopped: AsyncCallback | None = None
    on_trip_end: AsyncCallback | None = None
    on_trip_offline_reconcile: AsyncCallback | None = None
    on_charging_complete: AsyncCallback | None = None
    on_sentry_activated: AsyncCallback | None = None
    on_sentry_deactivated: AsyncSentryDeactivatedCallback | None = None
    on_sentry_recording: AsyncCallback | None = None
    on_departure_safety_alert: AsyncCallback | None = None
    on_tire_pressure_alert: AsyncCallback | None = None
    on_charging_issue_alert: AsyncCallback | None = None
    on_position_update: AsyncPositionCallback | None = None

    client: mqtt.Client | None = None
    vehicle_state: VehicleState = field(default_factory=VehicleState)
    _loop: asyncio.AbstractEventLoop | None = None
    _manual_disconnect: bool = False

    def connect(self) -> None:
        """连接 MQTT 服务器"""
        if self.client:
            return

        logger.info(f"正在连接 MQTT 服务器: {config.mqtt_url}")
        self._manual_disconnect = False

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
            failure_monitor.record_mqtt_disconnected(f"连接异常: {e}")
            logger.exception(f"MQTT 连接失败: {e}")
            self.client = None

    def disconnect(self) -> None:
        """断开 MQTT 连接"""
        if self.client:
            logger.info("正在断开 MQTT 连接...")
            self._manual_disconnect = True
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
            failure_monitor.record_mqtt_connected()
            logger.info("MQTT 连接成功")
            topic = f"teslamate/cars/{self.car_id}/#"
            client.subscribe(topic)
            logger.info(f"已订阅主题: {topic}")
        else:
            failure_monitor.record_mqtt_disconnected(f"连接失败: {reason_code}")
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
        if self._manual_disconnect:
            logger.info("MQTT 主动断开连接")
            return

        failure_monitor.record_mqtt_disconnected(f"reason_code={reason_code}")
        logger.warning(f"MQTT 连接断开: {reason_code}")

    def _on_message(
        self, client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage
    ) -> None:
        """消息回调"""
        topic_name = msg.topic.split("/")[-1]
        payload = msg.payload.decode("utf-8")

        logger.debug(f"MQTT 收到: {topic_name} = {payload}")

        if topic_name == "state":
            self._handle_drive_state(payload)
        elif topic_name == "charging_state":
            self._handle_charging_state(payload)
        elif topic_name == "sentry_mode":
            self._handle_sentry_mode(payload)
        elif topic_name == "is_user_present":
            self._handle_user_presence(payload)
        elif topic_name == "center_display_state":
            self._handle_center_display_state(payload)
        elif topic_name == "latitude":
            self._update_float_value("latitude", payload)
        elif topic_name == "longitude":
            self._update_float_value("longitude", payload)
        elif topic_name == "geofence":
            self.vehicle_state.geofence = payload or None
        elif topic_name == "battery_level":
            self._handle_battery_level(payload)
        elif topic_name == "rated_battery_range_km":
            self._handle_rated_range(payload)
        elif topic_name == "charge_enable_request":
            self._handle_charge_enable_request(payload)
        elif topic_name == "charge_energy_added":
            self._handle_charge_energy_added(payload)
        elif topic_name == "charge_limit_soc":
            self._handle_charge_limit_soc(payload)
        elif topic_name == "charger_power":
            self._handle_charger_power(payload)
        elif topic_name == "plugged_in":
            self._handle_plugged_in(payload)
        elif topic_name == "charge_port_door_open":
            self._handle_charge_port_door(payload)
        elif topic_name == "locked":
            self._handle_simple_bool_topic("locked", "locked", payload)
        elif topic_name == "windows_open":
            self._handle_simple_bool_topic("windows_open", "windows_open", payload)
        elif topic_name == "doors_open":
            self._handle_simple_bool_topic("doors_open", "doors_open", payload)
        elif topic_name == "trunk_open":
            self._handle_simple_bool_topic("trunk_open", "trunk_open", payload)
        elif topic_name == "frunk_open":
            self._handle_simple_bool_topic("frunk_open", "frunk_open", payload)
        elif topic_name in {
            "tpms_pressure_fl",
            "tpms_pressure_fr",
            "tpms_pressure_rl",
            "tpms_pressure_rr",
        }:
            self._update_float_value(topic_name, payload)
        elif topic_name in {
            "tpms_soft_warning_fl",
            "tpms_soft_warning_fr",
            "tpms_soft_warning_rl",
            "tpms_soft_warning_rr",
        }:
            self._handle_tpms_warning(topic_name, payload)

    def _handle_drive_state(self, payload: str) -> None:
        """处理车辆状态变化"""
        prev_state = self.vehicle_state.state
        self.vehicle_state.state = payload
        if prev_state != payload:
            self.vehicle_state.drive_state_version += 1
        state_version = self.vehicle_state.drive_state_version

        logger.info(f"state 变化: {prev_state} -> {payload}")

        if payload == "driving" and prev_state != "driving":
            logger.info(f"检测到行程开始: {prev_state} -> {payload}")
            if self.on_trip_started and self._loop:
                self._schedule_callback(self.on_trip_started)

        if prev_state == "driving" and payload in ("online", "asleep"):
            logger.info(f"检测到行程结束: {prev_state} -> {payload}")
            if self.on_trip_stopped and self._loop:
                self._schedule_callback(self.on_trip_stopped)
            if self.on_trip_end and self._loop:
                logger.info("将在 3 秒后触发行程结束处理")
                asyncio.run_coroutine_threadsafe(
                    self._delayed_trip_end(),
                    self._loop,
                )

        if prev_state == "driving" and payload == "offline":
            logger.info("检测到车辆驾驶中离线，进入行程补偿窗口")
            if self.on_trip_offline_reconcile and self._loop:
                logger.debug(
                    "将在 "
                    f"{config.trip_offline_reconcile_delay} 秒后执行行程离线补偿检查"
                )
                asyncio.run_coroutine_threadsafe(
                    self._delayed_trip_offline_reconcile(state_version),
                    self._loop,
                )

    def _handle_charging_state(self, payload: str) -> None:
        """处理充电状态变化"""
        prev_state = self.vehicle_state.charging_state
        self.vehicle_state.charging_state = payload
        if prev_state != payload:
            self.vehicle_state.charging_state_version += 1
        state_version = self.vehicle_state.charging_state_version

        logger.info(f"charging_state 变化: {prev_state} -> {payload}")

        is_initial = self._mark_initialized("charging_state")
        if payload == "Charging":
            self.vehicle_state.charging_session_had_active_charge = True
            self.vehicle_state.charging_stop_pending_confirmation = False
            self._reset_charging_issue_state()
            return

        if payload == "Starting":
            self.vehicle_state.charging_stop_pending_confirmation = False
            return

        if payload == "NoPower":
            if not is_initial:
                self.vehicle_state.charging_stop_pending_confirmation = False
                self.vehicle_state.pending_charging_issue = True
                self._maybe_emit_charging_issue()
            return

        if payload == "Disconnected":
            if (
                not is_initial
                and self.vehicle_state.charging_session_had_active_charge
                and self.vehicle_state.active_charging_issue is None
                and prev_state in {"Charging", "Stopped"}
            ):
                logger.info(f"检测到充电结束: {prev_state} -> {payload}")
                self._schedule_charging_complete()
                self.vehicle_state.charging_session_had_active_charge = False
                self.vehicle_state.charging_stop_pending_confirmation = False
            else:
                self.vehicle_state.charging_session_had_active_charge = False
                self.vehicle_state.charging_stop_pending_confirmation = False

            self._reset_charging_issue_state()
            return

        if payload == "Complete":
            self._reset_charging_issue_state()
            if not is_initial and self.vehicle_state.charging_session_had_active_charge:
                logger.info(f"检测到充电结束: {prev_state} -> {payload}")
                self._schedule_charging_complete()
                self.vehicle_state.charging_session_had_active_charge = False
                self.vehicle_state.charging_stop_pending_confirmation = False
            return

        if (
            not is_initial
            and prev_state == "Charging"
            and payload == "Stopped"
        ):
            if self._should_treat_stopped_as_complete():
                logger.info(f"检测到充电结束: {prev_state} -> {payload}")
                self._schedule_charging_complete()
                self.vehicle_state.charging_session_had_active_charge = False
                self.vehicle_state.charging_stop_pending_confirmation = False
            else:
                logger.info("检测到充电停止，进入异常确认窗口")
                self.vehicle_state.charging_stop_pending_confirmation = True
                self._schedule_charging_stopped_confirmation(state_version)

    def _handle_sentry_mode(self, payload: str) -> None:
        """处理哨兵模式变化"""
        prev_sentry, current_sentry, is_initial = self._update_bool_state(
            "sentry_mode",
            "sentry_mode",
            payload,
        )
        if current_sentry is None:
            return

        if is_initial:
            if current_sentry:
                self.vehicle_state.sentry_activated_at = datetime.now()
                self.vehicle_state.sentry_activated_battery_level = (
                    self.vehicle_state.battery_level
                )
                self.vehicle_state.sentry_activated_rated_range_km = (
                    self.vehicle_state.rated_range_km
                )
            return

        if not prev_sentry and current_sentry:
            logger.info("========== 哨兵模式已激活 ==========")
            self.vehicle_state.sentry_activated_at = datetime.now()
            self.vehicle_state.sentry_activated_battery_level = (
                self.vehicle_state.battery_level
            )
            self.vehicle_state.sentry_activated_rated_range_km = (
                self.vehicle_state.rated_range_km
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

            rated_range_drop_km = None
            start_rated_range_km = self.vehicle_state.sentry_activated_rated_range_km
            end_rated_range_km = self.vehicle_state.rated_range_km
            if start_rated_range_km is not None and end_rated_range_km is not None:
                rated_range_drop_km = max(start_rated_range_km - end_rated_range_km, 0.0)

            self.vehicle_state.sentry_activated_battery_level = None
            self.vehicle_state.sentry_activated_rated_range_km = None
            self.vehicle_state.sentry_recording_count = 0
            self.vehicle_state.last_sentry_event_time = None

            if self.on_sentry_deactivated and self._loop:
                self._schedule_sentry_deactivated(
                    duration_min=duration_min,
                    battery_drop=battery_drop,
                    rated_range_km=end_rated_range_km,
                    rated_range_drop_km=rated_range_drop_km,
                    recording_count=recording_count,
                )

    def _handle_user_presence(self, payload: str) -> None:
        """处理用户在车状态变化"""
        prev_value, current_value, is_initial = self._update_bool_state(
            "is_user_present",
            "is_user_present",
            payload,
        )
        if current_value is None or is_initial:
            return

        if current_value:
            self.vehicle_state.departure_check_session_id += 1
            self.vehicle_state.departure_alert_sent = False
            return

        if prev_value is True and not current_value:
            self.vehicle_state.departure_check_session_id += 1
            self.vehicle_state.departure_alert_sent = False
            session_id = self.vehicle_state.departure_check_session_id

            if self.on_departure_safety_alert and self._loop:
                logger.info("检测到用户离车，准备执行延迟安全检查")
                asyncio.run_coroutine_threadsafe(
                    self._delayed_departure_safety_check(session_id),
                    self._loop,
                )

    def _handle_battery_level(self, payload: str) -> None:
        """处理电量更新"""
        battery_level = self._parse_int(payload)
        if battery_level is None:
            return

        self.vehicle_state.battery_level = battery_level
        if (
            self.vehicle_state.sentry_mode
            and self.vehicle_state.sentry_activated_battery_level is None
        ):
            self.vehicle_state.sentry_activated_battery_level = battery_level

        self._maybe_emit_charging_issue()

    def _handle_rated_range(self, payload: str) -> None:
        """处理表显续航更新。"""
        rated_range_km = self._parse_float(payload)
        if rated_range_km is None:
            return

        self.vehicle_state.rated_range_km = rated_range_km
        if (
            self.vehicle_state.sentry_mode
            and self.vehicle_state.sentry_activated_rated_range_km is None
        ):
            self.vehicle_state.sentry_activated_rated_range_km = rated_range_km

    def _handle_charge_limit_soc(self, payload: str) -> None:
        """处理充电上限更新"""
        charge_limit_soc = self._parse_int(payload)
        if charge_limit_soc is None:
            return

        self.vehicle_state.charge_limit_soc = charge_limit_soc
        self._maybe_emit_charging_issue()

    def _handle_charge_enable_request(self, payload: str) -> None:
        """处理充电启停请求状态。"""
        _, current_value, _ = self._update_bool_state(
            "charge_enable_request",
            "charge_enable_request",
            payload,
        )
        if current_value is None:
            return

        self._maybe_emit_charging_issue()

    def _handle_charge_energy_added(self, payload: str) -> None:
        """处理本次充电累计电量。"""
        charge_energy_added = self._parse_float(payload)
        if charge_energy_added is None:
            return

        self.vehicle_state.charge_energy_added = charge_energy_added
        if charge_energy_added > 0:
            self.vehicle_state.charging_session_had_active_charge = True

    def _handle_charger_power(self, payload: str) -> None:
        """处理充电功率更新"""
        charger_power = self._parse_float(payload)
        if charger_power is None:
            return

        self.vehicle_state.charger_power = charger_power
        self._maybe_emit_charging_issue()

    def _handle_plugged_in(self, payload: str) -> None:
        """处理插枪状态更新"""
        _, current_value, _ = self._update_bool_state("plugged_in", "plugged_in", payload)
        if current_value is None:
            return

        if current_value is False and self.vehicle_state.charging_state == "Stopped":
            if (
                self.vehicle_state.charging_stop_pending_confirmation
                and self.vehicle_state.charging_session_had_active_charge
            ):
                logger.info("检测到停止后拔枪，按充电完成处理")
                self._schedule_charging_complete()
                self.vehicle_state.charging_session_had_active_charge = False
                self.vehicle_state.charging_stop_pending_confirmation = False
            self._reset_charging_issue_state()
            return

        self._maybe_emit_charging_issue()

    def _handle_charge_port_door(self, payload: str) -> None:
        """处理充电口门状态更新"""
        self._handle_simple_bool_topic(
            "charge_port_door_open",
            "charge_port_door_open",
            payload,
        )

    def _handle_tpms_warning(self, topic_name: str, payload: str) -> None:
        """处理胎压软告警状态"""
        _, current_value, is_initial = self._update_bool_state(
            topic_name,
            topic_name,
            payload,
        )
        if current_value is None:
            return

        if not current_value and not self._get_active_tire_warnings():
            self.vehicle_state.last_tire_alert_time = None
            return

        if not is_initial and current_value:
            self._maybe_emit_tire_pressure_alert()

    def _handle_center_display_state(self, payload: str) -> None:
        """处理 TeslaMate 发布的 center_display_state。

        TeslaMate 前端将 center_display_state == 7 解释为 “Sentry Mode recording”。
        首次收到 retained 快照时只建立基线，不直接触发推送，避免服务重启后误报。
        """
        current_state = self._parse_int(payload)
        if current_state is None:
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

    def _maybe_emit_tire_pressure_alert(self) -> None:
        """在冷却窗口外触发胎压异常推送。"""
        if not config.tpms_notify_enabled or not self.on_tire_pressure_alert or not self._loop:
            return

        warning_wheels = self._get_active_tire_warnings()
        if not warning_wheels:
            self.vehicle_state.last_tire_alert_time = None
            return

        now = datetime.now()
        if (
            self.vehicle_state.last_tire_alert_time is not None
            and (now - self.vehicle_state.last_tire_alert_time).total_seconds()
            <= config.tpms_notify_cooldown
        ):
            logger.info("胎压异常在冷却窗口内，跳过推送")
            return

        logger.info(f"检测到胎压异常: {warning_wheels}")
        self._schedule_callback(self.on_tire_pressure_alert)
        self.vehicle_state.last_tire_alert_time = now

    def _maybe_emit_charging_issue(self) -> None:
        """在满足条件时触发充电异常提醒。"""
        if (
            not config.charging_issue_notify_enabled
            or not self.on_charging_issue_alert
            or not self._loop
            or not self.vehicle_state.pending_charging_issue
        ):
            return

        issue_key = self._build_charging_issue_key()
        if issue_key is None:
            return

        now = datetime.now()
        if (
            self.vehicle_state.last_charging_issue_key == issue_key
            and self.vehicle_state.last_charging_issue_time is not None
            and (now - self.vehicle_state.last_charging_issue_time).total_seconds()
            <= config.charging_issue_cooldown
        ):
            logger.info("充电异常在冷却窗口内，跳过推送")
            return

        logger.info(f"检测到充电异常: {issue_key}")
        self.vehicle_state.active_charging_issue = issue_key
        self.vehicle_state.last_charging_issue_key = issue_key
        self.vehicle_state.last_charging_issue_time = now
        self.vehicle_state.pending_charging_issue = False
        self._schedule_callback(self.on_charging_issue_alert)

    def _should_treat_stopped_as_complete(self) -> bool:
        """判断 Stopped 是否更应视为正常结束而非异常。"""
        if not self.vehicle_state.charging_session_had_active_charge:
            return True

        if self.vehicle_state.charge_enable_request is False:
            return True

        return self._build_charging_issue_key() != "stopped_early"

    def _build_charging_issue_key(self) -> str | None:
        """根据当前充电上下文生成异常类型。"""
        if self.vehicle_state.charging_state == "NoPower":
            return "no_power"

        if (
            self.vehicle_state.charging_state == "Stopped"
            and self.vehicle_state.charging_session_had_active_charge
            and self.vehicle_state.plugged_in is True
            and self.vehicle_state.battery_level is not None
            and self.vehicle_state.charge_limit_soc is not None
        ):
            soc_gap = self.vehicle_state.charge_limit_soc - self.vehicle_state.battery_level
            if soc_gap >= config.charging_stopped_min_soc_gap:
                return "stopped_early"

        return None

    def _reset_charging_issue_state(self, *, reset_cooldown: bool = False) -> None:
        """在充电恢复正常后清空异常状态。"""
        self.vehicle_state.pending_charging_issue = False
        self.vehicle_state.active_charging_issue = None
        if reset_cooldown:
            self.vehicle_state.last_charging_issue_key = None
            self.vehicle_state.last_charging_issue_time = None

    def _schedule_callback(self, callback: AsyncCallback) -> None:
        """将协程安全切回主事件循环执行。"""
        if not self._loop:
            return

        self._loop.call_soon_threadsafe(self._create_task, callback())

    def _schedule_charging_complete(self) -> None:
        """延迟调度充电完成处理，等待 TeslaMate 完成落库。"""
        if not self.on_charging_complete or not self._loop:
            return

        logger.info("将在 10 秒后触发充电完成处理")
        asyncio.run_coroutine_threadsafe(
            self._delayed_charging_complete(),
            self._loop,
        )

    def _schedule_charging_stopped_confirmation(self, expected_state_version: int) -> None:
        """对 Stopped 进行延迟确认，区分主动停充和异常中断。"""
        if not self._loop:
            return

        asyncio.run_coroutine_threadsafe(
            self._delayed_charging_stopped_confirmation(expected_state_version),
            self._loop,
        )

    def _schedule_sentry_deactivated(
        self,
        duration_min: float | None,
        battery_drop: int | None,
        rated_range_km: float | None,
        rated_range_drop_km: float | None,
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
                rated_range_km,
                rated_range_drop_km,
                recording_count,
            ),
        )

    @staticmethod
    def _create_task(coro: Coroutine[Any, Any, None]) -> None:
        """统一创建异步任务，便于从线程安全地投递到事件循环。"""
        asyncio.create_task(coro)

    def _mark_initialized(self, topic_key: str) -> bool:
        """记录主题首次快照。"""
        if topic_key in self.vehicle_state.initialized_topics:
            return False

        self.vehicle_state.initialized_topics.add(topic_key)
        return True

    def _update_bool_state(
        self,
        topic_key: str,
        attr_name: str,
        payload: str,
    ) -> tuple[bool | None, bool | None, bool]:
        """解析并更新布尔状态。"""
        current_value = self._parse_bool(payload)
        if current_value is None:
            logger.debug(f"解析 {topic_key} 失败: {payload}")
            return None, None, False

        prev_value = getattr(self.vehicle_state, attr_name)
        setattr(self.vehicle_state, attr_name, current_value)
        is_initial = self._mark_initialized(topic_key)
        return prev_value, current_value, is_initial

    def _handle_simple_bool_topic(
        self,
        topic_key: str,
        attr_name: str,
        payload: str,
    ) -> None:
        """处理仅需更新缓存的布尔主题。"""
        self._update_bool_state(topic_key, attr_name, payload)

    def _update_float_value(self, attr_name: str, payload: str) -> None:
        """更新浮点类型缓存字段。"""
        current_value = self._parse_float(payload)
        if current_value is None:
            logger.debug(f"解析 {attr_name} 失败: {payload}")
            return

        setattr(self.vehicle_state, attr_name, current_value)
        if (
            attr_name in {"latitude", "longitude"}
            and self.on_position_update
            and self._loop
        ):
            self._loop.call_soon_threadsafe(
                self._create_task,
                self.on_position_update(
                    self.vehicle_state.latitude,
                    self.vehicle_state.longitude,
                ),
            )

    @staticmethod
    def _parse_bool(payload: str) -> bool | None:
        """解析 MQTT 布尔值。"""
        value = payload.lower()
        if value == "true":
            return True
        if value == "false":
            return False
        return None

    @staticmethod
    def _parse_int(payload: str) -> int | None:
        """解析 MQTT 整数值。"""
        try:
            return int(payload)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_float(payload: str) -> float | None:
        """解析 MQTT 浮点值。"""
        try:
            return float(payload)
        except (TypeError, ValueError):
            return None

    async def _delayed_departure_safety_check(self, session_id: int) -> None:
        """离车后延迟检查门窗和锁车状态，降低误报。"""
        await asyncio.sleep(config.departure_safety_delay)

        if session_id != self.vehicle_state.departure_check_session_id:
            return

        if self.vehicle_state.is_user_present is not False:
            return

        if self.vehicle_state.departure_alert_sent:
            return

        issues = self.get_departure_safety_issues()
        if not issues:
            logger.info("离车安全检查通过，无需推送")
            return

        self.vehicle_state.departure_alert_sent = True
        if config.departure_safety_notify_enabled and self.on_departure_safety_alert:
            await self.on_departure_safety_alert()
        else:
            logger.debug("离车安全提醒未启用，跳过推送")

    def get_departure_safety_issues(self) -> list[str]:
        """获取当前离车后的安全风险列表。"""
        issues: list[str] = []

        if self.vehicle_state.locked is False:
            issues.append("车辆未锁定")
        if self.vehicle_state.windows_open is True:
            issues.append("车窗未关闭")
        if self.vehicle_state.doors_open is True:
            issues.append("车门未关闭")
        if self.vehicle_state.trunk_open is True:
            issues.append("后备箱未关闭")
        if self.vehicle_state.frunk_open is True:
            issues.append("前备箱未关闭")
        if (
            self.vehicle_state.charge_port_door_open is True
            and self.vehicle_state.plugged_in is not True
        ):
            issues.append("充电口未关闭")

        return issues

    def _get_active_tire_warnings(self) -> list[str]:
        """获取当前处于软告警的轮胎列表。"""
        wheels: list[str] = []
        if self.vehicle_state.tpms_soft_warning_fl is True:
            wheels.append(TIRE_LABELS["fl"])
        if self.vehicle_state.tpms_soft_warning_fr is True:
            wheels.append(TIRE_LABELS["fr"])
        if self.vehicle_state.tpms_soft_warning_rl is True:
            wheels.append(TIRE_LABELS["rl"])
        if self.vehicle_state.tpms_soft_warning_rr is True:
            wheels.append(TIRE_LABELS["rr"])
        return wheels

    def get_tire_pressure_snapshot(self) -> tuple[list[str], dict[str, float | None]]:
        """获取胎压告警轮位和各轮胎当前压力。"""
        pressures = {
            TIRE_LABELS["fl"]: self.vehicle_state.tpms_pressure_fl,
            TIRE_LABELS["fr"]: self.vehicle_state.tpms_pressure_fr,
            TIRE_LABELS["rl"]: self.vehicle_state.tpms_pressure_rl,
            TIRE_LABELS["rr"]: self.vehicle_state.tpms_pressure_rr,
        }
        return self._get_active_tire_warnings(), pressures

    def get_current_charging_issue(self) -> str | None:
        """获取当前充电异常类型。"""
        return self._build_charging_issue_key() or self.vehicle_state.active_charging_issue

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

    async def _delayed_trip_offline_reconcile(self, expected_state_version: int) -> None:
        """在地下车库等弱网场景下，为超时结案提供延迟补偿检查。"""
        logger.debug(
            f"等待 {config.trip_offline_reconcile_delay} 秒后执行行程离线补偿检查..."
        )
        await asyncio.sleep(float(config.trip_offline_reconcile_delay))

        if self.vehicle_state.drive_state_version != expected_state_version:
            logger.debug("行程离线补偿窗口内状态已变化，取消本次补偿检查")
            return

        if self.vehicle_state.state != "offline":
            logger.debug("车辆状态已不再是 offline，跳过本次补偿检查")
            return

        logger.info("离线补偿窗口结束，开始执行行程补偿检查")
        if self.on_trip_offline_reconcile:
            await self.on_trip_offline_reconcile()

    async def _delayed_charging_stopped_confirmation(self, expected_state_version: int) -> None:
        """延迟确认 Stopped，避免把刚插枪和主动停充误判为异常。"""
        logger.debug(
            f"等待 {CHARGING_STOP_CONFIRM_DELAY_SEC:.0f} 秒确认充电是否异常停止..."
        )
        await asyncio.sleep(CHARGING_STOP_CONFIRM_DELAY_SEC)

        if self.vehicle_state.charging_state_version != expected_state_version:
            logger.debug("充电状态已变化，取消本次异常停止确认")
            return

        if not self.vehicle_state.charging_stop_pending_confirmation:
            logger.debug("当前无待确认的停止事件，跳过异常停止确认")
            return

        if self.vehicle_state.charging_state != "Stopped":
            logger.debug("充电状态已不再是 Stopped，跳过异常停止确认")
            return

        if self.vehicle_state.plugged_in is not True:
            logger.info("充电停止后已断开连接，按充电完成处理")
            self._schedule_charging_complete()
            self.vehicle_state.charging_session_had_active_charge = False
            self.vehicle_state.charging_stop_pending_confirmation = False
            self._reset_charging_issue_state()
            return

        if self._should_treat_stopped_as_complete():
            logger.info("充电停止确认后判定为正常结束，发送充电完成通知")
            self._schedule_charging_complete()
            self.vehicle_state.charging_session_had_active_charge = False
            self.vehicle_state.charging_stop_pending_confirmation = False
            self._reset_charging_issue_state()
            return

        logger.info("充电停止确认后仍未恢复，按异常停止处理")
        self.vehicle_state.charging_stop_pending_confirmation = False
        self.vehicle_state.pending_charging_issue = True
        self._maybe_emit_charging_issue()
        if self.vehicle_state.active_charging_issue is not None:
            self.vehicle_state.charging_session_had_active_charge = False

    async def _delayed_charging_complete(self) -> None:
        """延迟触发充电完成处理"""
        logger.info("等待 10 秒后触发充电完成处理...")
        await asyncio.sleep(10.0)
        logger.info("延迟结束，开始触发充电完成处理")
        if self.on_charging_complete:
            await self.on_charging_complete()

    async def get_location_str(self) -> str | None:
        """获取当前位置字符串"""
        if self.vehicle_state.geofence:
            return self.vehicle_state.geofence

        if (
            self.vehicle_state.latitude is None
            or self.vehicle_state.longitude is None
        ):
            return None

        try:
            from tesla_notifier.integrations.amap import reverse_geocode

            address = await reverse_geocode(
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
