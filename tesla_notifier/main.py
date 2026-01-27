"""主入口"""

import asyncio
import signal
import sys

from dotenv import load_dotenv

# 加载 .env 文件（必须在导入 config 之前）
load_dotenv()

from tesla_notifier import bark, database
from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger
from tesla_notifier.mqtt_handler import MqttHandler
from tesla_notifier.scheduler import Scheduler
from tesla_notifier.state import push_state
from tesla_notifier.weather import generate_weather_suggestion, get_weather

logger = setup_logger("main")

# 全局状态
mqtt_handler: MqttHandler | None = None
scheduler: Scheduler | None = None
_shutdown_event: asyncio.Event | None = None


async def handle_trip_end() -> None:
    """处理行程结束（带重试机制）

    使用 state-based 检测后，TeslaMate 已确认行程结束并写入数据库，
    只需少量重试即可获取完整数据。

    能耗计算使用 cars.efficiency 动态值，比固定 150 Wh/km 更准确。
    """
    logger.info("========== 检测到行程结束 ==========")

    max_retries = 2  # state-based 检测后数据应该已就绪
    retry_delay = 2.0  # 缩短重试间隔

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"尝试获取行程数据 (第 {attempt}/{max_retries} 次)")
            trip = await database.get_latest_trip(config.car_id)

            # 检查行程数据是否就绪（end_date 不为空表示行程已完成）
            if not trip or not trip.end_date:
                if attempt < max_retries:
                    logger.warning(
                        f"行程数据未就绪 (trip={'存在' if trip else '不存在'}, end_date={trip.end_date if trip else 'N/A'})，{retry_delay}秒后重试"
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    logger.error("行程数据获取失败，已达最大重试次数")
                    return

            # 去重检查
            if push_state.is_trip_pushed(trip.id):
                logger.info(f"该行程已推送过，跳过: {trip.id}")
                return

            # 短行程过滤
            if trip.distance < config.min_trip_distance:
                logger.info(
                    f"行程距离过短，跳过推送: {trip.distance:.1f} km < {config.min_trip_distance} km"
                )
                return

            logger.info(f"准备推送行程数据: id={trip.id}, distance={trip.distance:.1f} km")

            # 获取车辆动态 efficiency 值
            car_efficiency = await database.get_car_efficiency(config.car_id)

            # 计算能耗和效率（使用动态 efficiency，处理负值）
            rated_range_used = max(trip.start_rated_range_km - trip.end_rated_range_km, 0)
            energy_used = (rated_range_used * car_efficiency) / 1000.0  # kWh
            efficiency = (rated_range_used * car_efficiency) / trip.distance if trip.distance > 0 else 0  # Wh/km

            # 获取驾驶评分
            score = await database.get_trip_driving_score(trip.id)

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
                hard_accel_count=score.hard_accel_count if score else None,
                hard_brake_count=score.hard_brake_count if score else None,
                driving_grade=score.grade if score else None,
                speed_avg=trip.speed_avg,
                speed_max=trip.speed_max,
                odometer=trip.odometer,
            )

            if success:
                push_state.mark_trip_pushed(trip.id)
                logger.info(f"行程推送成功: {trip.id}")
            else:
                logger.error(f"行程推送失败: {trip.id}")

            return  # 成功处理，退出重试循环

        except Exception as e:
            logger.exception(f"处理行程结束异常: {e}")
            if attempt < max_retries:
                logger.info(f"发生异常，{retry_delay}秒后重试 ({attempt}/{max_retries})")
                await asyncio.sleep(retry_delay)
            else:
                logger.error("处理行程结束失败，已达最大重试次数")


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
                start_range=charging.start_rated_range_km,
                end_range=charging.end_rated_range_km,
                outside_temp=charging.outside_temp_avg,
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
            from tesla_notifier.weather import WeatherData

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

        success = await bark.send_sentry_activated(
            location=location,
            battery_level=battery_level,
        )

        if success:
            logger.info("哨兵激活推送成功")
        else:
            logger.error("哨兵激活推送失败")

    except Exception as e:
        logger.exception(f"处理哨兵激活异常: {e}")


async def handle_sentry_deactivated(duration_min: float | None = None) -> None:
    """处理哨兵模式关闭"""
    logger.info("========== 检测到哨兵模式关闭 ==========")

    try:
        success = await bark.send_sentry_deactivated(duration_min=duration_min)

        if success:
            logger.info("哨兵关闭推送成功")
        else:
            logger.error("哨兵关闭推送失败")

    except Exception as e:
        logger.exception(f"处理哨兵关闭异常: {e}")


async def handle_sentry_recording(power_w: float) -> None:
    """处理哨兵录制事件

    当哨兵模式下检测到功率跳变时触发，表示可能有人经过触发了录制。

    Args:
        power_w: 触发时的功率（W）
    """
    logger.info(f"========== 检测到哨兵录制事件: {power_w:.0f}W ==========")

    try:
        # 获取位置和电量信息
        location = await mqtt_handler.get_location_str() if mqtt_handler else None
        battery_level = mqtt_handler.vehicle_state.battery_level if mqtt_handler else None

        success = await bark.send_sentry_recording(
            power_w=power_w,
            location=location,
            battery_level=battery_level,
        )

        if success:
            logger.info("哨兵录制推送成功")
        else:
            logger.error("哨兵录制推送失败")

    except Exception as e:
        logger.exception(f"处理哨兵录制异常: {e}")


async def run() -> None:
    """运行服务"""
    global mqtt_handler, scheduler

    logger.info("========== Tesla Notifier 启动 ==========")

    # 配置验证
    errors = config.validate()
    if errors:
        for err in errors:
            logger.error(f"配置错误: {err}")
        logger.error("请检查配置后重新启动")
        sys.exit(1)

    logger.info(f"配置信息:")
    logger.info(f"  ENABLE_CRON: {config.cron_enabled}")
    logger.info(f"  ENABLE_MQTT: {config.mqtt_enabled}")
    logger.info(f"  DAILY_CRON: {config.daily_cron}")
    logger.info(f"  WEEKLY_CRON: {config.weekly_cron}")
    logger.info(f"  MONTHLY_CRON: {config.monthly_cron}")
    logger.info(f"  CAR_ID: {config.car_id}")
    logger.info(f"  MIN_TRIP_DISTANCE: {config.min_trip_distance}")
    logger.info(f"  MQTT_URL: {config.mqtt_url if config.mqtt_enabled else '(未启用)'}")
    logger.info(f"  BARK_KEY: {'(已配置)' if config.bark_key else '(未配置)'}")
    logger.info(f"  CAIYUN_TOKEN: {'(已配置)' if config.caiyun_token else '(未配置)'}")
    logger.info(f"  AMAP_KEY: {'(已配置)' if config.amap_key else '(未配置)'}")
    logger.info(f"  TZ: {config.timezone}")
    logger.debug(f"  SENTRY_NOTIFY_ENABLED: 哨兵录制{'(已开启)' if config.sentry_notify_enabled else '(已关闭)'}")
    if config.sentry_notify_enabled:
        logger.debug(f"  SENTRY_POWER_THRESHOLD: 功率阈值{config.sentry_power_threshold}W")
        logger.debug(f"  SENTRY_RECORDING_COOLDOWN: 防抖间隔{config.sentry_recording_cooldown}s")

    # 初始化数据库连接池
    await database.init_pool()

    loop = asyncio.get_event_loop()

    # 启动 MQTT
    if config.mqtt_enabled:
        logger.info("启动 MQTT 订阅...")
        mqtt_handler = MqttHandler(
            car_id=config.car_id,
            on_trip_end=handle_trip_end,
            on_charging_complete=handle_charging_complete,
            on_sentry_activated=handle_sentry_activated,
            on_sentry_deactivated=handle_sentry_deactivated,
            on_sentry_recording=handle_sentry_recording,
        )
        mqtt_handler.set_event_loop(loop)
        mqtt_handler.connect()
    else:
        logger.warning("MQTT 订阅未启用 (设置 ENABLE_MQTT=true 启用)")

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
    global mqtt_handler, scheduler

    logger.info("========== 正在停止服务 ==========")

    # 停止定时任务
    if scheduler:
        try:
            scheduler.stop()
        except Exception as e:
            logger.error(f"停止定时任务失败: {e}")

    # 断开 MQTT 连接
    if mqtt_handler:
        try:
            mqtt_handler.disconnect()
        except Exception as e:
            logger.error(f"断开 MQTT 连接失败: {e}")

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
