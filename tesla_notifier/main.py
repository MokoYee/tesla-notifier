# ruff: noqa: E402
"""主入口"""

import asyncio
import signal
import sys
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# 加载 .env 文件（必须在导入 config 之前）
load_dotenv()

from tesla_notifier.analytics.traffic import TrafficSampler
from tesla_notifier.config import config
from tesla_notifier.integrations.grafana import (
    build_charge_details_url,
    build_drive_details_url,
)
from tesla_notifier.integrations.weather import (
    generate_weather_suggestion,
    get_weather,
)
from tesla_notifier.logger import setup_logger
from tesla_notifier.notifications import bark
from tesla_notifier.runtime.health import SystemAlert, failure_monitor
from tesla_notifier.runtime.mqtt_freshness import (
    MqttFreshnessMonitor,
    parse_teslamate_since,
    run_mqtt_freshness_monitor,
)
from tesla_notifier.runtime.mqtt_handler import MqttHandler
from tesla_notifier.runtime.scheduler import Scheduler
from tesla_notifier.storage import database
from tesla_notifier.storage.state import push_state

logger = setup_logger("main")

# 全局状态
mqtt_handler: MqttHandler | None = None
scheduler: Scheduler | None = None
traffic_sampler: TrafficSampler | None = None
trip_compensation_task: asyncio.Task[None] | None = None
mqtt_freshness_task: asyncio.Task[None] | None = None
mqtt_freshness_monitor: MqttFreshnessMonitor | None = None
_shutdown_event: asyncio.Event | None = None
_trip_processing_lock: asyncio.Lock | None = None

TRIP_COMPENSATION_RECENT_LIMIT = 5
TRIP_COMPENSATION_STARTUP_DELAY = 30.0


def _get_trip_processing_lock() -> asyncio.Lock:
    """获取行程推送互斥锁，避免实时推送与补偿推送并发重复发送。"""
    global _trip_processing_lock
    if _trip_processing_lock is None:
        _trip_processing_lock = asyncio.Lock()
    return _trip_processing_lock


def _parse_trip_end_time(
    trip: database.TripData | database.TripCandidate,
) -> datetime | None:
    """解析行程结束时间，失败时返回 None。"""
    if not trip.end_date:
        return None

    try:
        return datetime.fromisoformat(trip.end_date)
    except ValueError:
        logger.warning(f"解析行程结束时间失败，跳过补偿判断: trip_id={trip.id}")
        return None


def _select_trip_candidate_for_compensation(
    trips: list[database.TripCandidate],
) -> database.TripCandidate | None:
    """从最近行程中挑选一个适合补偿推送的候选项。"""
    cutoff = datetime.now(UTC) - timedelta(hours=config.trip_compensation_max_age_hours)

    for trip in trips:
        if push_state.is_trip_pushed(trip.id):
            continue

        if trip.distance < config.min_trip_distance:
            continue

        end_time = _parse_trip_end_time(trip)
        if end_time is None:
            continue

        if end_time < cutoff:
            logger.debug(
                "候选行程已超出补偿窗口，停止继续补偿: "
                f"id={trip.id}, end_time={trip.end_date}"
            )
            return None

        return trip

    return None


def _should_skip_trip_compensation(trigger_reason: str) -> bool:
    """行驶中不执行补偿检查，避免把上一段旧行程在当前行驶期间补发。"""
    if mqtt_handler and mqtt_handler.vehicle_state.state == "driving":
        logger.debug(f"车辆当前仍在 driving，跳过本次行程补偿检查: {trigger_reason}")
        return True

    return False


async def _send_trip_notification(
    trip: database.TripData,
    trigger_reason: str,
    *,
    include_traffic_summary: bool,
) -> bool:
    """发送单条行程通知。"""
    logger.info(
        "准备推送行程数据: "
        f"id={trip.id}, distance={trip.distance:.1f} km, reason={trigger_reason}"
    )

    car_efficiency = await database.get_car_efficiency(config.car_id)

    rated_range_used = max(trip.start_rated_range_km - trip.end_rated_range_km, 0)
    energy_used = (rated_range_used * car_efficiency) / 1000.0
    efficiency = (
        (rated_range_used * car_efficiency) / trip.distance if trip.distance > 0 else 0
    )

    trip_traffic_summary = None
    if include_traffic_summary and traffic_sampler is not None:
        await traffic_sampler.wait_for_stop_finalize()
        trip_traffic_summary = await traffic_sampler.consume_finished_summary()
    elif traffic_sampler is not None:
        await traffic_sampler.discard_active_trip()

    score = await database.get_trip_driving_score(
        trip.id,
        traffic_summary=trip_traffic_summary,
    )

    success = await bark.send_trip_end(
        start_address=trip.start_address,
        end_address=trip.end_address,
        start_time=trip.start_date,
        end_time=trip.end_date,
        distance=trip.distance,
        duration=trip.duration_min,
        energy_used=energy_used,
        efficiency=efficiency,
        start_range=trip.start_rated_range_km,
        end_range=trip.end_rated_range_km,
        start_soc=trip.start_battery_level,
        end_soc=trip.end_battery_level,
        outside_temp=trip.outside_temp_avg,
        driving_score=score.score if score else None,
        driving_label=score.label if score else None,
        trip_commentary=score.trip_commentary if score else None,
        key_factors=score.key_factors if score else None,
        speed_avg=trip.speed_avg,
        speed_max=trip.speed_max,
        odometer=trip.odometer,
        trip_id=trip.id,
        detail_url=build_drive_details_url(
            drive_id=trip.id,
            car_id=trip.car_id,
            start_time=trip.start_date,
            end_time=trip.end_date,
        ),
    )

    if success:
        push_state.mark_trip_pushed(trip.id)
        logger.info(f"行程推送成功: {trip.id}, reason={trigger_reason}")
    else:
        logger.error(f"行程推送失败: {trip.id}, reason={trigger_reason}")

    return success


async def reconcile_trip_notification(trigger_reason: str) -> bool:
    """补偿检查最近已结束但未推送的行程。"""
    if _should_skip_trip_compensation(trigger_reason):
        return False

    async with _get_trip_processing_lock():
        candidates = await database.get_recent_trip_candidates(
            config.car_id,
            limit=TRIP_COMPENSATION_RECENT_LIMIT,
        )
        if not candidates:
            logger.debug(f"未查询到可补偿的最近行程: {trigger_reason}")
            return False

        candidate = _select_trip_candidate_for_compensation(candidates)
        if candidate is None:
            logger.debug(f"最近行程均无需补偿推送: {trigger_reason}")
            return False

        trip = await database.get_trip_by_id(candidate.id)
        if trip is None:
            logger.warning(f"候选行程详情加载失败，跳过补偿: trip_id={candidate.id}")
            return False

        return await _send_trip_notification(
            trip,
            trigger_reason,
            include_traffic_summary=False,
        )


async def run_trip_compensation_worker() -> None:
    """后台巡检最近结束但未推送的行程，兜底 MQTT 弱网和服务重启场景。"""
    logger.info("行程补偿巡检任务已启动")

    try:
        await asyncio.sleep(TRIP_COMPENSATION_STARTUP_DELAY)

        trigger_reason = "startup-reconcile"

        while True:
            try:
                await reconcile_trip_notification(trigger_reason)
            except Exception as e:
                logger.exception(f"行程补偿巡检执行异常: {e}")

            trigger_reason = "periodic-reconcile"
            await asyncio.sleep(float(config.trip_compensation_interval))
    except asyncio.CancelledError:
        logger.info("行程补偿巡检任务已停止")
        raise


def handle_mqtt_monitor_message(topic_name: str, payload: str) -> None:
    """把 MQTT 消息时间同步给新鲜度监控。"""
    if mqtt_freshness_monitor is None:
        return

    since_at = parse_teslamate_since(payload) if topic_name == "since" else None
    mqtt_freshness_monitor.record_mqtt_message(since_at=since_at)


def _current_local_time() -> str:
    """返回当前本地时间。"""
    return datetime.now(ZoneInfo(config.timezone)).strftime("%H:%M")


def _current_local_token() -> str:
    """返回当前本地时间令牌。"""
    return datetime.now(ZoneInfo(config.timezone)).strftime("%Y%m%d%H%M%S")


def _current_date_tag() -> str:
    """返回当前本地日期标签。"""
    return datetime.now(ZoneInfo(config.timezone)).strftime("%Y%m%d")


def _current_month_tag() -> str:
    """返回当前本地月份标签。"""
    return datetime.now(ZoneInfo(config.timezone)).strftime("%Y%m")


def _format_datetime_tag(value: datetime | None) -> str | None:
    """将 datetime 转成稳定标签。"""
    if value is None:
        return None
    return value.strftime("%Y%m%d%H%M%S")


def _get_enabled_feature_labels() -> list[str]:
    """汇总当前已启用的用户能力。"""
    labels: list[str] = []

    if config.mqtt_enabled:
        labels.append("实时事件")
    if config.cron_enabled:
        labels.append("日报周报月报")
    if config.sentry_notify_enabled:
        labels.append("哨兵事件")
    if config.departure_safety_notify_enabled:
        labels.append("离车安全")
    if config.tpms_notify_enabled:
        labels.append("胎压提醒")
    if config.charging_issue_notify_enabled:
        labels.append("充电异常")
    if config.traffic_analysis_enabled and config.amap_key:
        labels.append("路况增强")

    return labels


def _get_weather_service_status() -> str:
    """返回天气服务当前状态描述。"""
    if config.amap_key:
        return "高德天气已启用，异常时自动回退 Open-Meteo"
    return "AMAP_KEY 未配置，当前使用 Open-Meteo"


def _get_traffic_sampler_status() -> str:
    """返回路况采样当前状态描述。"""
    if not config.traffic_analysis_enabled:
        return "未启用（TRAFFIC_ANALYSIS_ENABLED=OFF）"
    if not config.amap_key:
        return "未启用（AMAP_KEY 未配置）"
    return (
        "已启用"
        f"（间隔 {config.traffic_sample_interval}s，"
        f"最小位移 {config.traffic_sample_min_distance_km} km，"
        f"查询半径 {config.traffic_query_radius} m）"
    )


def _mqtt_connection_status() -> str:
    """返回 MQTT 连接状态文本。"""
    if not config.mqtt_enabled:
        return "未启用"
    if mqtt_handler and mqtt_handler.client and mqtt_handler.client.is_connected():
        return "已连接"
    return "未连接"


async def handle_system_alert(alert: SystemAlert) -> None:
    """统一处理系统链路告警。"""
    title = "⚠️ 系统告警" if alert.status == "alert" else "✅ 系统恢复"
    lines = [
        f"🕐 {_current_local_time()}",
        f"🔧 组件 {alert.component}",
    ]

    if alert.details:
        lines.append("")
        lines.extend([f"• {detail}" for detail in alert.details])

    success = await bark.send_system_status(
        title=title,
        subtitle=alert.summary,
        lines=lines,
        event_id=bark.build_event_id("system", alert.event_key, _current_local_token()),
        priority=alert.severity,
        reason=alert.reason,
    )

    if success:
        logger.info(f"系统通知发送成功: {alert.summary}")
    else:
        logger.error(f"系统通知发送失败: {alert.summary}")


async def notify_startup_db_init_failure(error: Exception) -> None:
    """按统一开关语义处理启动期数据库初始化失败告警。"""
    if not config.failure_alert_notify_enabled:
        logger.warning("数据库初始化失败，但 FAILURE_ALERT_NOTIFY_ENABLED 已关闭")
        return

    await handle_system_alert(
        SystemAlert(
            component="database",
            status="alert",
            severity="high",
            summary="数据库初始化失败",
            reason="应用启动阶段无法建立数据库连接",
            details=(f"错误详情 {error}",),
            event_key="database-init-failure",
        )
    )


async def send_startup_health_check() -> None:
    """启动完成后发送一次系统自检。"""
    if not config.system_health_notify_enabled:
        return

    if config.mqtt_enabled:
        await asyncio.sleep(3)

    db_ok = await database.ping()
    mqtt_status = _mqtt_connection_status()
    core_ready = db_ok and (not config.mqtt_enabled or mqtt_status == "已连接")
    enabled_features = "、".join(_get_enabled_feature_labels()) or "基础推送"
    traffic_status = "已开启" if config.traffic_analysis_enabled and config.amap_key else "未开启"

    lines = [
        f"🕐 {_current_local_time()}",
        "",
        "【链路】",
        "• Bark 已配置",
        f"• 数据库 {'正常' if db_ok else '异常'}",
        f"• MQTT {mqtt_status}",
        "",
        "【能力】",
        f"• 已启用 {enabled_features}",
        f"• 高德地址 {'已配置' if config.amap_key else '未配置'}",
        f"• 路况增强 {traffic_status}",
    ]

    success = await bark.send_system_status(
        title="🩺 启动自检",
        subtitle="核心链路正常" if core_ready else "存在待关注项",
        lines=lines,
        event_id=bark.build_event_id("health-startup", _current_local_token()),
        priority="medium" if core_ready else "high",
        reason="应用启动完成后的链路自检结果",
    )

    if success:
        logger.info("启动自检推送成功")
    else:
        logger.error("启动自检推送失败")


async def handle_trip_end() -> None:
    """处理行程结束（带重试机制）

    使用 state-based 检测后，TeslaMate 已确认行程结束并写入数据库，
    只需少量重试即可获取完整数据。

    能耗计算使用 cars.efficiency 动态值，比固定 150 Wh/km 更准确。
    """
    logger.info("========== 检测到行程结束 ==========")

    max_retries = 2  # state-based 检测后数据应该已就绪
    retry_delay = 2.0  # 缩短重试间隔

    async with _get_trip_processing_lock():
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"尝试获取行程数据 (第 {attempt}/{max_retries} 次)")
                trip = await database.get_latest_trip(config.car_id)

                if not trip or not trip.end_date:
                    if attempt < max_retries:
                        logger.warning(
                            "行程数据未就绪 "
                            f"(trip={'存在' if trip else '不存在'}, "
                            f"end_date={trip.end_date if trip else 'N/A'})，"
                            f"{retry_delay}秒后重试"
                        )
                        await asyncio.sleep(retry_delay)
                        continue

                    logger.error("行程数据获取失败，已达最大重试次数")
                    return

                if push_state.is_trip_pushed(trip.id):
                    logger.info(f"该行程已推送过，跳过: {trip.id}")
                    return

                if trip.distance < config.min_trip_distance:
                    logger.info(
                        "行程距离过短，跳过推送: "
                        f"{trip.distance:.1f} km < {config.min_trip_distance} km"
                    )
                    return

                await _send_trip_notification(
                    trip,
                    "mqtt-state-end",
                    include_traffic_summary=True,
                )
                return
            except Exception as e:
                logger.exception(f"处理行程结束异常: {e}")
                if attempt < max_retries:
                    logger.info(f"发生异常，{retry_delay}秒后重试 ({attempt}/{max_retries})")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error("处理行程结束失败，已达最大重试次数")


async def handle_trip_started() -> None:
    """处理行程开始，启动路况采样。"""
    if traffic_sampler is None:
        return

    await traffic_sampler.start_trip()


async def handle_trip_stopped() -> None:
    """处理行程结束边缘事件，先冻结路况采样结果。"""
    if traffic_sampler is None:
        return

    await traffic_sampler.stop_trip()


async def handle_trip_offline_reconcile() -> None:
    """在 TeslaMate 进入 driving/offline 超时窗口后执行一次延迟补偿检查。"""
    logger.info("========== 开始执行离线行程补偿检查 ==========")
    await reconcile_trip_notification("mqtt-offline-reconcile")


async def handle_position_update(
    latitude: float | None,
    longitude: float | None,
) -> None:
    """同步 MQTT 最新坐标到路况采样器。"""
    if traffic_sampler is None:
        return

    await traffic_sampler.update_position(latitude, longitude)


async def handle_charging_complete() -> None:
    """处理充电完成（带重试机制）

    由于 MQTT 事件触发后 TeslaMate 可能还未完成数据库写入，
    采用重试机制确保能获取到完整的充电数据。
    """
    logger.info("========== 检测到充电完成 ==========")

    max_retries = 3
    retry_delay = 5.0  # 秒

    for attempt in range(1, max_retries + 1):
        try:
            charging = await database.get_latest_charging(config.car_id)

            # 检查充电数据是否就绪
            if not charging or not charging.end_date:
                if attempt < max_retries:
                    logger.info(
                        f"充电数据未就绪，{retry_delay}秒后重试 ({attempt}/{max_retries})"
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    logger.warning("充电数据获取失败，已达最大重试次数")
                    return

            # 去重检查
            if push_state.is_charge_pushed(charging.id):
                logger.info(f"该充电记录已推送过，跳过: {charging.id}")
                return

            logger.info(
                f"准备推送充电数据: id={charging.id}, energy={charging.charge_energy_added:.1f} kWh"
            )

            success = await bark.send_charging_complete(
                location=charging.location,
                start_time=charging.start_date,
                end_time=charging.end_date,
                duration=charging.duration_min,
                start_soc=charging.start_battery_level,
                end_soc=charging.end_battery_level,
                energy_added=charging.charge_energy_added,
                peak_power=charging.charger_power_max,
                charge_type=charging.charge_type,
                charging_efficiency=charging.charging_efficiency,
                start_range=charging.start_rated_range_km,
                end_range=charging.end_rated_range_km,
                outside_temp=charging.outside_temp_avg,
                charging_id=charging.id,
                detail_url=build_charge_details_url(
                    charging_process_id=charging.id,
                    car_id=charging.car_id,
                    start_time=charging.start_date,
                    end_time=charging.end_date,
                ),
            )

            if success:
                push_state.mark_charge_pushed(charging.id)
                logger.info(f"充电推送成功: {charging.id}")
            else:
                logger.error(f"充电推送失败: {charging.id}")

            return  # 成功处理，退出重试循环

        except Exception as e:
            logger.exception(f"处理充电完成异常: {e}")
            if attempt < max_retries:
                logger.info(f"发生异常，{retry_delay}秒后重试 ({attempt}/{max_retries})")
                await asyncio.sleep(retry_delay)
            else:
                logger.error("处理充电完成失败，已达最大重试次数")


async def send_daily_briefing_task() -> None:
    """每日简报任务"""
    logger.info("========== 开始执行每日简报任务 ==========")

    try:
        position = await database.get_vehicle_last_position(config.car_id)
        location = "未知位置"
        weather = None

        if position:
            position_id, latitude, longitude = position
            weather = await get_weather(latitude, longitude)

            # 使用 resolve_location_name 解析地址（优先级：geofence > 实时匹配 > 高德 API）
            async with database.get_connection() as conn:
                location = await database.resolve_location_name(
                    conn, None, position_id, None
                )

        if not weather:
            logger.warning("无法获取天气数据，使用默认值")
            from tesla_notifier.integrations.weather import WeatherData

            weather = WeatherData(
                condition="未知",
                temp=20.0,
                temp_min=15.0,
                temp_max=25.0,
                humidity=50,
            )

        yesterday = await database.get_yesterday_summary(config.car_id)
        suggestion = generate_weather_suggestion(weather)

        success = await bark.send_daily_briefing(
            location=location,
            weather_condition=weather.condition,
            temp=weather.temp,
            temp_min=weather.temp_min,
            temp_max=weather.temp_max,
            humidity=weather.humidity,
            yesterday_trips=yesterday.total_trips if yesterday else None,
            yesterday_distance=yesterday.total_distance if yesterday else None,
            yesterday_efficiency=yesterday.avg_efficiency if yesterday else None,
            suggestion=suggestion,
            period_tag=_current_date_tag(),
        )

        if success:
            logger.info("每日简报推送成功")
        else:
            logger.error("每日简报推送失败")

    except Exception as e:
        logger.exception(f"每日简报任务异常: {e}")

    logger.info("========== 每日简报任务执行完毕 ==========")


async def send_weekly_report_task() -> None:
    """周报任务"""
    logger.info("========== 开始执行周报任务 ==========")

    try:
        summary = await database.get_weekly_summary(config.car_id)

        if not summary:
            logger.info("本周无行程数据，跳过周报")
            return

        logger.info(f"周报数据汇总: {summary}")

        success = await bark.send_weekly_report(
            total_trips=summary.total_trips,
            total_distance=summary.total_distance,
            avg_efficiency=summary.avg_efficiency,
            longest_trip=summary.longest_trip,
            total_duration_min=summary.total_duration_min,
            total_charging_count=summary.total_charging_count,
            total_energy_added=summary.total_energy_added,
            avg_speed=summary.avg_speed,
            max_speed=summary.max_speed,
            period_tag=_current_date_tag(),
        )

        if success:
            logger.info("周报推送成功")
        else:
            logger.error("周报推送失败")

    except Exception as e:
        logger.exception(f"周报任务异常: {e}")

    logger.info("========== 周报任务执行完毕 ==========")


async def send_monthly_report_task() -> None:
    """月报任务"""
    logger.info("========== 开始执行月报任务 ==========")

    try:
        summary = await database.get_monthly_summary(config.car_id)

        if not summary:
            logger.info("上月无行程数据，跳过月报")
            return

        logger.info(f"月报数据汇总: {summary}")

        success = await bark.send_monthly_report(
            total_trips=summary.total_trips,
            total_distance=summary.total_distance,
            avg_efficiency=summary.avg_efficiency,
            longest_trip=summary.longest_trip,
            period_tag=_current_month_tag(),
        )

        if success:
            logger.info("月报推送成功")
        else:
            logger.error("月报推送失败")

    except Exception as e:
        logger.exception(f"月报任务异常: {e}")

    logger.info("========== 月报任务执行完毕 ==========")


async def handle_sentry_activated() -> None:
    """处理哨兵模式激活"""
    logger.info("========== 检测到哨兵模式激活 ==========")

    try:
        # 获取位置（优先地址，失败则坐标）
        location = await mqtt_handler.get_location_str() if mqtt_handler else None
        battery_level = mqtt_handler.vehicle_state.battery_level if mqtt_handler else None
        rated_range_km = mqtt_handler.vehicle_state.rated_range_km if mqtt_handler else None

        success = await bark.send_sentry_activated(
            location=location,
            battery_level=battery_level,
            rated_range_km=rated_range_km,
            session_tag=_format_datetime_tag(
                mqtt_handler.vehicle_state.sentry_activated_at if mqtt_handler else None
            ),
        )

        if success:
            logger.info("哨兵激活推送成功")
        else:
            logger.error("哨兵激活推送失败")

    except Exception as e:
        logger.exception(f"处理哨兵激活异常: {e}")


async def handle_sentry_deactivated(
    duration_min: float | None = None,
    battery_drop: int | None = None,
    rated_range_km: float | None = None,
    rated_range_drop_km: float | None = None,
    recording_count: int = 0,
) -> None:
    """处理哨兵模式关闭"""
    logger.info("========== 检测到哨兵模式关闭 ==========")

    try:
        # 获取当前位置和当前电量
        location = await mqtt_handler.get_location_str() if mqtt_handler else None
        battery_level = mqtt_handler.vehicle_state.battery_level if mqtt_handler else None

        success = await bark.send_sentry_deactivated(
            location=location,
            duration_min=duration_min,
            battery_level=battery_level,
            rated_range_km=rated_range_km,
            battery_drop=battery_drop,
            rated_range_drop_km=rated_range_drop_km,
            recording_count=recording_count,
            session_tag=_current_local_token(),
        )

        if success:
            logger.info("哨兵关闭推送成功")
        else:
            logger.error("哨兵关闭推送失败")

    except Exception as e:
        logger.exception(f"处理哨兵关闭异常: {e}")


async def handle_sentry_recording() -> None:
    """处理哨兵录制事件

    仅使用 TeslaMate 实时状态触发，避免引入不可靠的推断逻辑。
    """
    logger.info("========== 检测到哨兵录制事件（实时状态） ==========")

    try:
        # 获取位置和电量信息
        location = await mqtt_handler.get_location_str() if mqtt_handler else None
        battery_level = mqtt_handler.vehicle_state.battery_level if mqtt_handler else None
        rated_range_km = mqtt_handler.vehicle_state.rated_range_km if mqtt_handler else None
        recording_count = (
            mqtt_handler.vehicle_state.sentry_recording_count if mqtt_handler else 0
        )

        success = await bark.send_sentry_recording(
            location=location,
            battery_level=battery_level,
            rated_range_km=rated_range_km,
            recording_count=recording_count,
            session_tag=_format_datetime_tag(
                mqtt_handler.vehicle_state.last_sentry_event_time if mqtt_handler else None
            ),
        )

        if success:
            logger.info("哨兵录制推送成功")
        else:
            logger.error("哨兵录制推送失败")

    except Exception as e:
        logger.exception(f"处理哨兵录制异常: {e}")


async def handle_departure_safety_alert() -> None:
    """处理离车安全提醒"""
    logger.info("========== 检测到离车安全风险 ==========")

    if not mqtt_handler:
        return

    try:
        issues = mqtt_handler.get_departure_safety_issues()
        if not issues:
            logger.info("离车安全检查已恢复正常，跳过提醒")
            return

        location = await mqtt_handler.get_location_str()
        battery_level = mqtt_handler.vehicle_state.battery_level

        success = await bark.send_departure_safety_alert(
            issues=issues,
            location=location,
            battery_level=battery_level,
            session_tag=str(mqtt_handler.vehicle_state.departure_check_session_id),
        )

        if success:
            logger.info("离车安全提醒推送成功")
        else:
            logger.error("离车安全提醒推送失败")

    except Exception as e:
        logger.exception(f"处理离车安全提醒异常: {e}")


async def handle_tire_pressure_alert() -> None:
    """处理胎压异常提醒"""
    logger.info("========== 检测到胎压异常 ==========")

    if not mqtt_handler:
        return

    try:
        warning_wheels, pressures = mqtt_handler.get_tire_pressure_snapshot()
        if not warning_wheels:
            logger.info("当前无胎压告警，跳过提醒")
            return

        location = await mqtt_handler.get_location_str()

        success = await bark.send_tire_pressure_alert(
            warning_wheels=warning_wheels,
            pressures=pressures,
            location=location,
            session_tag=_current_local_token(),
        )

        if success:
            logger.info("胎压异常推送成功")
        else:
            logger.error("胎压异常推送失败")

    except Exception as e:
        logger.exception(f"处理胎压异常提醒异常: {e}")


async def handle_charging_issue_alert() -> None:
    """处理充电异常提醒"""
    logger.info("========== 检测到充电异常 ==========")

    if not mqtt_handler:
        return

    try:
        issue_type = mqtt_handler.get_current_charging_issue()
        if not issue_type:
            logger.info("当前无充电异常，跳过提醒")
            return

        location = await mqtt_handler.get_location_str()
        state = mqtt_handler.vehicle_state

        success = await bark.send_charging_issue_alert(
            issue_type=issue_type,
            location=location,
            battery_level=state.battery_level,
            charge_limit_soc=state.charge_limit_soc,
            charger_power=state.charger_power,
            plugged_in=state.plugged_in,
            session_tag=_format_datetime_tag(state.last_charging_issue_time),
        )

        if success:
            logger.info("充电异常推送成功")
        else:
            logger.error("充电异常推送失败")

    except Exception as e:
        logger.exception(f"处理充电异常提醒异常: {e}")


async def run() -> None:
    """运行服务"""
    global mqtt_handler, scheduler, traffic_sampler, trip_compensation_task
    global mqtt_freshness_monitor, mqtt_freshness_task, _trip_processing_lock

    logger.info("========== Tesla Notifier 启动 ==========")

    # 配置验证
    errors = config.validate()
    if errors:
        for err in errors:
            logger.error(f"配置错误: {err}")
        logger.error("请检查配置后重新启动")
        sys.exit(1)

    logger.info("配置信息:")
    logger.info(f"  ENABLE_CRON: {config.cron_enabled}")
    logger.info(f"  ENABLE_MQTT: {config.mqtt_enabled}")
    logger.info(f"  DAILY_CRON: {config.daily_cron}")
    logger.info(f"  WEEKLY_CRON: {config.weekly_cron}")
    logger.info(f"  MONTHLY_CRON: {config.monthly_cron}")
    logger.info(f"  CAR_ID: {config.car_id}")
    logger.info(f"  MIN_TRIP_DISTANCE: {config.min_trip_distance}")
    logger.info(f"  MQTT_URL: {config.mqtt_url if config.mqtt_enabled else '(未启用)'}")
    logger.info(f"  BARK_KEY: {'(已配置)' if config.bark_key else '(未配置)'}")
    logger.info(f"  GRAFANA_BASE_URL: {'(已配置)' if config.grafana_base_url else '(未配置)'}")
    logger.info(f"  AMAP_KEY: {'(已配置)' if config.amap_key else '(未配置)'}")
    logger.info(f"  TZ: {config.timezone}")
    logger.info(f"天气服务状态: {_get_weather_service_status()}")
    logger.info(
        "高德逆地理编码: "
        f"{'已启用' if config.amap_key else '未启用（将使用 TeslaMate 原始地址）'}"
    )
    logger.info(f"高德路况采样: {_get_traffic_sampler_status()}")
    logger.debug(
        f"  TRIP_COMPENSATION_INTERVAL: {config.trip_compensation_interval}s"
    )
    logger.debug(
        "  TRIP_OFFLINE_RECONCILE_DELAY: "
        f"{config.trip_offline_reconcile_delay}s"
    )
    logger.debug(
        "  TRIP_COMPENSATION_MAX_AGE_HOURS: "
        f"{config.trip_compensation_max_age_hours}h"
    )
    traffic_status = (
        "(已开启)"
        if config.traffic_analysis_enabled and config.amap_key
        else "(已关闭)"
    )
    logger.debug(f"  TRAFFIC_ANALYSIS_ENABLED: 行程路况采样{traffic_status}")
    if config.traffic_analysis_enabled:
        logger.debug(f"  TRAFFIC_SAMPLE_INTERVAL: {config.traffic_sample_interval}s")
        logger.debug(
            "  TRAFFIC_SAMPLE_MIN_DISTANCE_KM: "
            f"{config.traffic_sample_min_distance_km} km"
        )
        logger.debug(f"  TRAFFIC_QUERY_RADIUS: {config.traffic_query_radius} m")
    sentry_status = "(已开启)" if config.sentry_notify_enabled else "(已关闭)"
    logger.debug(f"  SENTRY_NOTIFY_ENABLED: 哨兵录制{sentry_status}")
    if config.sentry_notify_enabled:
        logger.debug(
            "  SENTRY_RECORDING_COOLDOWN: "
            f"防抖间隔{config.sentry_recording_cooldown}s"
        )
    departure_status = "(已开启)" if config.departure_safety_notify_enabled else "(已关闭)"
    logger.debug(f"  DEPARTURE_SAFETY_NOTIFY_ENABLED: 离车安全{departure_status}")
    if config.departure_safety_notify_enabled:
        logger.debug(f"  DEPARTURE_SAFETY_DELAY: {config.departure_safety_delay}s")
        logger.debug(
            "  DEPARTURE_SAFETY_COOLDOWN: "
            f"{config.departure_safety_cooldown}s"
        )
    tpms_status = "(已开启)" if config.tpms_notify_enabled else "(已关闭)"
    logger.debug(f"  TPMS_NOTIFY_ENABLED: 胎压异常{tpms_status}")
    if config.tpms_notify_enabled:
        logger.debug(f"  TPMS_NOTIFY_COOLDOWN: {config.tpms_notify_cooldown}s")
    charging_issue_status = (
        "(已开启)" if config.charging_issue_notify_enabled else "(已关闭)"
    )
    logger.debug(f"  CHARGING_ISSUE_NOTIFY_ENABLED: 充电异常{charging_issue_status}")
    if config.charging_issue_notify_enabled:
        logger.debug(
            f"  CHARGING_ISSUE_COOLDOWN: {config.charging_issue_cooldown}s"
        )
        logger.debug(
            "  CHARGING_NO_POWER_GRACE_PERIOD: "
            f"{config.charging_no_power_grace_period}s"
        )
        logger.debug(
            "  CHARGING_STOPPED_MIN_SOC_GAP: "
            f"{config.charging_stopped_min_soc_gap}%"
        )
    logger.debug(
        "  SYSTEM_HEALTH_NOTIFY_ENABLED: "
        f"{'(已开启)' if config.system_health_notify_enabled else '(已关闭)'}"
    )
    logger.debug(
        "  FAILURE_ALERT_NOTIFY_ENABLED: "
        f"{'(已开启)' if config.failure_alert_notify_enabled else '(已关闭)'}"
    )
    logger.debug(
        f"  DB_FAILURE_ALERT_THRESHOLD: {config.db_failure_alert_threshold}"
    )
    logger.debug(
        f"  MQTT_DISCONNECT_ALERT_AFTER: {config.mqtt_disconnect_alert_after}s"
    )
    logger.debug(
        "  MQTT_FRESHNESS_MONITOR_ENABLED: "
        f"{'(已开启)' if config.mqtt_freshness_monitor_enabled else '(已关闭)'}"
    )
    if config.mqtt_freshness_monitor_enabled:
        logger.debug(
            "  MQTT_FRESHNESS_CHECK_INTERVAL: "
            f"{config.mqtt_freshness_check_interval}s"
        )
        logger.debug(
            "  MQTT_FRESHNESS_STALE_AFTER: "
            f"{config.mqtt_freshness_stale_after}s"
        )
        logger.debug(
            "  MQTT_FRESHNESS_DB_ACTIVE_WINDOW: "
            f"{config.mqtt_freshness_db_active_window}s"
        )

    loop = asyncio.get_event_loop()
    failure_monitor.configure(loop, handle_system_alert)
    failure_monitor.start()

    # 初始化数据库连接池
    try:
        await database.init_pool()
    except Exception as e:
        logger.exception(f"数据库初始化失败: {e}")
        await notify_startup_db_init_failure(e)
        raise

    # 启动 MQTT
    if config.mqtt_enabled:
        logger.info("启动 MQTT 订阅...")
        traffic_sampler = TrafficSampler()
        mqtt_handler = MqttHandler(
            car_id=config.car_id,
            on_trip_started=handle_trip_started,
            on_trip_stopped=handle_trip_stopped,
            on_trip_end=handle_trip_end,
            on_trip_offline_reconcile=handle_trip_offline_reconcile,
            on_charging_complete=handle_charging_complete,
            on_sentry_activated=handle_sentry_activated,
            on_sentry_deactivated=handle_sentry_deactivated,
            on_sentry_recording=handle_sentry_recording,
            on_departure_safety_alert=handle_departure_safety_alert,
            on_tire_pressure_alert=handle_tire_pressure_alert,
            on_charging_issue_alert=handle_charging_issue_alert,
            on_position_update=handle_position_update,
            on_mqtt_message=handle_mqtt_monitor_message,
        )
        mqtt_handler.set_event_loop(loop)
        mqtt_handler.connect()
        trip_compensation_task = asyncio.create_task(run_trip_compensation_worker())
        if config.mqtt_freshness_monitor_enabled:
            mqtt_freshness_monitor = MqttFreshnessMonitor(
                car_id=config.car_id,
                latest_position_time_provider=database.get_latest_position_time,
                alert_handler=handle_system_alert,
                stale_after_seconds=config.mqtt_freshness_stale_after,
                db_active_window_seconds=config.mqtt_freshness_db_active_window,
            )
            mqtt_freshness_task = asyncio.create_task(
                run_mqtt_freshness_monitor(mqtt_freshness_monitor)
            )
    else:
        logger.warning("MQTT 订阅未启用 (设置 ENABLE_MQTT=true 启用)")
        trip_compensation_task = None
        mqtt_freshness_monitor = None
        mqtt_freshness_task = None

    # 启动定时任务
    if config.cron_enabled:
        logger.info("启动定时任务...")
        scheduler = Scheduler()
        scheduler.add_daily_task(send_daily_briefing_task)
        scheduler.add_weekly_task(send_weekly_report_task)
        scheduler.add_monthly_task(send_monthly_report_task)
        scheduler.start()
    else:
        logger.warning("定时任务未启用 (设置 ENABLE_CRON=true 启用)")

    asyncio.create_task(send_startup_health_check())
    logger.info("========== Tesla Notifier 启动完成 ==========")

    # 初始化退出事件
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    # 等待退出信号
    try:
        await _shutdown_event.wait()
        logger.info("收到退出信号，开始清理...")
    except asyncio.CancelledError:
        logger.info("任务被取消，开始清理...")
    finally:
        # 确保资源被清理
        await cleanup()


async def cleanup() -> None:
    """清理所有资源（统一的清理函数）"""
    global mqtt_handler, scheduler, traffic_sampler, trip_compensation_task
    global mqtt_freshness_monitor, mqtt_freshness_task

    logger.info("========== 正在停止服务 ==========")

    # 停止定时任务
    if scheduler:
        try:
            scheduler.stop()
        except Exception as e:
            logger.error(f"停止定时任务失败: {e}")

    if trip_compensation_task:
        trip_compensation_task.cancel()
        with suppress(asyncio.CancelledError):
            await trip_compensation_task
        trip_compensation_task = None

    if mqtt_freshness_task:
        mqtt_freshness_task.cancel()
        with suppress(asyncio.CancelledError):
            await mqtt_freshness_task
        mqtt_freshness_task = None
        mqtt_freshness_monitor = None

    try:
        await failure_monitor.shutdown()
    except Exception as e:
        logger.error(f"停止健康监控失败: {e}")

    # 断开 MQTT 连接
    if mqtt_handler:
        try:
            mqtt_handler.disconnect()
        except Exception as e:
            logger.error(f"断开 MQTT 连接失败: {e}")

    # 停止路况采样
    if traffic_sampler:
        try:
            await traffic_sampler.shutdown()
        except Exception as e:
            logger.error(f"停止路况采样失败: {e}")

    # 关闭数据库连接池
    try:
        await database.close_pool()
    except Exception as e:
        logger.error(f"关闭数据库连接池失败: {e}")

    logger.info("========== 服务已停止 ==========")


def shutdown() -> None:
    """关闭服务（同步部分）- 已废弃，使用 cleanup() 代替"""
    pass


async def shutdown_async() -> None:
    """关闭服务（异步部分）- 已废弃，使用 cleanup() 代替"""
    pass


def main() -> None:
    """主入口"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler(sig: int, _frame: object) -> None:
        """信号处理器"""
        logger.info(f"收到信号 {sig}，准备退出...")
        # 在事件循环中设置退出事件
        if _shutdown_event is not None and loop.is_running():
            loop.call_soon_threadsafe(_shutdown_event.set)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        loop.run_until_complete(run())
    except KeyboardInterrupt:
        # 如果被中断，确保清理
        logger.info("收到键盘中断，退出...")
    finally:
        # 清理事件循环
        try:
            # 取消所有未完成的任务
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            # 等待所有任务取消完成
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        finally:
            loop.close()


if __name__ == "__main__":
    main()
