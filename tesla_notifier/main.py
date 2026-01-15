"""主入口"""

import asyncio
import signal
import sys

from tesla_notifier import bark, database
from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger
from tesla_notifier.mqtt_handler import MqttHandler
from tesla_notifier.scheduler import Scheduler
from tesla_notifier.weather import generate_weather_suggestion, get_weather

logger = setup_logger("main")

# 全局状态
mqtt_handler: MqttHandler | None = None
scheduler: Scheduler | None = None
pushed_trips: set[int] = set()
pushed_charges: set[int] = set()


async def handle_trip_end() -> None:
    """处理行程结束"""
    logger.info("========== 检测到行程结束 ==========")

    try:
        trip = await database.get_latest_trip(config.car_id)

        if not trip:
            logger.warning("未找到最新行程数据")
            return

        # 去重检查
        if trip.id in pushed_trips:
            logger.info(f"该行程已推送过，跳过: {trip.id}")
            return

        # 短行程过滤
        if trip.distance < config.min_trip_distance:
            logger.info(
                f"行程距离过短，跳过推送: {trip.distance:.1f} km < {config.min_trip_distance} km"
            )
            return

        logger.info(f"准备推送行程数据: id={trip.id}, distance={trip.distance:.1f} km")

        # 计算能耗和效率
        energy_used = (trip.start_rated_range_km - trip.end_rated_range_km) * 0.15
        efficiency = (energy_used * 1000) / trip.distance if trip.distance > 0 else 0

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
            hard_accel_pct=score.hard_accel_pct if score else None,
            hard_brake_pct=score.hard_brake_pct if score else None,
            driving_grade=score.grade if score else None,
        )

        if success:
            pushed_trips.add(trip.id)
            logger.info(f"行程推送成功: {trip.id}")
        else:
            logger.error(f"行程推送失败: {trip.id}")

    except Exception as e:
        logger.exception(f"处理行程结束异常: {e}")


async def handle_charging_complete() -> None:
    """处理充电完成"""
    logger.info("========== 检测到充电完成 ==========")

    try:
        charging = await database.get_latest_charging(config.car_id)

        if not charging:
            logger.warning("未找到最新充电记录")
            return

        # 去重检查
        if charging.id in pushed_charges:
            logger.info(f"该充电记录已推送过，跳过: {charging.id}")
            return

        logger.info(f"准备推送充电数据: id={charging.id}, energy={charging.charge_energy_added:.1f} kWh")

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
            pushed_charges.add(charging.id)
            logger.info(f"充电推送成功: {charging.id}")
        else:
            logger.error(f"充电推送失败: {charging.id}")

    except Exception as e:
        logger.exception(f"处理充电完成异常: {e}")


async def send_daily_briefing_task() -> None:
    """每日简报任务"""
    logger.info("========== 开始执行每日简报任务 ==========")

    try:
        position = await database.get_vehicle_last_position(config.car_id)
        location = "未知位置"
        weather = None

        if position:
            latitude, longitude = position
            weather = await get_weather(latitude, longitude)
            location = f"{latitude:.4f}, {longitude:.4f}"

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

        # 获取昨日驾驶评分
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(config.timezone)
        from datetime import datetime

        yesterday_date = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
        score = await database.get_daily_driving_score(config.car_id, yesterday_date)

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
            hard_accel_pct=score.hard_accel_pct if score else None,
            hard_brake_pct=score.hard_brake_pct if score else None,
            driving_grade=score.grade if score else None,
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
        location = mqtt_handler.get_location_str() if mqtt_handler else None
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


async def run() -> None:
    """运行服务"""
    global mqtt_handler, scheduler

    logger.info("========== Tesla Notifier 启动 ==========")
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
    logger.info(f"  TZ: {config.timezone}")

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

    # 保持运行
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass


def shutdown() -> None:
    """关闭服务"""
    global mqtt_handler, scheduler

    logger.info("========== 正在停止服务 ==========")

    if scheduler:
        scheduler.stop()

    if mqtt_handler:
        mqtt_handler.disconnect()

    logger.info("========== 服务已停止 ==========")


def main() -> None:
    """主入口"""
    # 信号处理
    def signal_handler(sig: int, frame: object) -> None:
        logger.info(f"收到信号 {sig}，准备退出...")
        shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
