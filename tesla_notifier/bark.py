"""Bark 推送通知客户端"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

import httpx

from tesla_notifier.config import config
from tesla_notifier.logger import log_with_data, setup_logger

logger = setup_logger("bark")

# 重试配置
MAX_RETRIES = 3  # 最大重试次数
RETRY_DELAY = 2  # 重试间隔（秒）


@dataclass
class BarkOptions:
    """Bark 推送选项"""

    title: str = "Tesla Notifier"
    body: str = ""
    sound: str = "bell"
    icon: str | None = None
    group: str = "tesla"
    url: str | None = None
    badge: int | None = None
    level: Literal["active", "timeSensitive", "passive"] = "active"


async def send_notification(options: BarkOptions) -> bool:
    """发送 Bark 推送（带重试机制）

    网络异常时自动重试，最多重试 3 次
    """
    if not config.bark_key:
        logger.warning("BARK_KEY 未配置，跳过推送")
        return False

    url = f"{config.bark_url}/{config.bark_key}"

    payload = {
        "title": options.title,
        "body": options.body,
        "sound": options.sound,
        "group": options.group,
        "level": options.level,
    }

    if options.icon:
        payload["icon"] = options.icon
    if options.url:
        payload["url"] = options.url
    if options.badge is not None:
        payload["badge"] = options.badge

    log_with_data(
        logger,
        logging.INFO,
        "准备发送推送",
        {"title": options.title, "group": options.group, "body_length": len(options.body)},
    )

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)

                if response.status_code != 200:
                    log_with_data(
                        logger,
                        logging.ERROR,
                        f"推送失败: HTTP {response.status_code}",
                        {"status_text": response.text},
                    )
                    return False

                result = response.json()

                if result.get("code") == 200:
                    logger.info("推送成功")
                    return True
                else:
                    log_with_data(logger, logging.ERROR, "推送返回错误", result)
                    return False

        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            # 网络相关异常，进行重试
            last_error = e
            if attempt < MAX_RETRIES:
                logger.warning(f"推送失败（第{attempt}次），{RETRY_DELAY}秒后重试: {e}")
                await asyncio.sleep(RETRY_DELAY)
            else:
                logger.error(f"推送失败，已重试{MAX_RETRIES}次: {e}")

        except Exception as e:
            # 其他异常，不重试
            logger.exception(f"推送异常: {e}")
            return False

    return False


async def send_trip_end(
    start_address: str,
    end_address: str,
    start_time: str,
    end_time: str,
    distance: float,
    duration: float,
    energy_used: float,
    efficiency: float,
    start_range: float,
    end_range: float,
    start_soc: int,
    end_soc: int,
    outside_temp: float | None = None,
    hard_accel_count: int | None = None,
    hard_brake_count: int | None = None,
    driving_grade: str | None = None,
    speed_avg: float | None = None,
    speed_max: float | None = None,
    odometer: float | None = None,
) -> bool:
    """发送行程结束推送"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(config.timezone)

    def format_time(iso_string: str) -> str:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%H:%M")

    def format_duration(minutes: float) -> str:
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        if hours > 0:
            return f"{hours}h {mins}min"
        return f"{mins}min"

    soc_diff = end_soc - start_soc
    soc_sign = "+" if soc_diff >= 0 else ""

    lines = [
        "━━━━━━━━━━━━━━━━",
        f"📍 {start_address} → {end_address}",
        "",
        f"🕐 {format_time(start_time)} - {format_time(end_time)} ({format_duration(duration)})",
        f"📏 里程: {distance:.1f} km",
    ]

    # 速度信息
    if speed_avg is not None and speed_max is not None:
        lines.append(f"🚀 均速 {speed_avg:.1f} km/h · 最高 {speed_max:.1f} km/h")

    # 能耗信息（只有在有效时才显示）
    if energy_used and energy_used > 0:
        lines.extend([
            f"⚡ 净能耗: {energy_used:.1f} kWh",
            f"📊 效率: {efficiency:.0f} Wh/km",
            "",
        ])
    else:
        lines.append("")

    if outside_temp is not None:
        lines.append(f"🌡️ 室外: {outside_temp:.1f}°C")

    range_diff = start_range - end_range
    range_sign = "-" if range_diff > 0 else "+"
    lines.extend([
        f"🔋 SoC: {start_soc}% → {end_soc}% ({soc_sign}{soc_diff}%)",
        f"📟 表显: {start_range:.0f} → {end_range:.0f} km ({range_sign}{abs(range_diff):.0f} km)",
    ])

    # 计算续航达成率
    range_consumed = float(start_range) - float(end_range)  # 表显消耗的续航
    if range_consumed > 0 and distance > 0:
        range_achievement_rate = (float(distance) / range_consumed) * 100
        lines.append(f"🎯 续航达成率: {range_achievement_rate:.1f}%")

    # 总里程
    if odometer is not None:
        lines.append(f"🛣️ 总里程: {odometer:.1f} km")

    if hard_accel_count is not None and hard_brake_count is not None and driving_grade is not None:
        lines.append(f"🏁 驾驶评分: 急加速{hard_accel_count}次 · 急减速{hard_brake_count}次（{driving_grade}）")

    lines.append("━━━━━━━━━━━━━━━━")

    return await send_notification(
        BarkOptions(
            title="🚗 行程结束",
            body="\n".join(lines),
            group="tesla-trip",
            icon=config.bark_icon,
            badge=1,
        )
    )


async def send_charging_complete(
    location: str,
    start_time: str,
    end_time: str,
    duration: float,
    start_soc: int,
    end_soc: int,
    energy_added: float,
    peak_power: float,
    start_range: float,
    end_range: float,
    outside_temp: float | None = None,
    cost: float | None = None,
) -> bool:
    """发送充电完成推送"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(config.timezone)

    def format_time(iso_string: str) -> str:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%H:%M")

    def format_duration(minutes: float) -> str:
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        if hours > 0:
            return f"{hours}h {mins}min"
        return f"{mins}min"

    lines = [
        "━━━━━━━━━━━━━━━━",
        f"⚡ {location}",
        "",
        f"🕐 {format_time(start_time)} - {format_time(end_time)} ({format_duration(duration)})",
        f"🔋 SoC: {start_soc}% → {end_soc}% (+{end_soc - start_soc}%)",
        f"⚡ 充入: {energy_added:.1f} kWh",
        f"📊 峰值功率: {peak_power:.0f} kW",
        f"📱 表显: {start_range:.0f} → {end_range:.0f} km",
    ]

    if outside_temp is not None:
        lines.append(f"🌡️ 室外: {outside_temp:.1f}°C")

    if cost is not None:
        lines.append(f"💰 费用: ¥{cost:.2f}")

    lines.append("━━━━━━━━━━━━━━━━")

    return await send_notification(
        BarkOptions(
            title="🔋 充电完成",
            body="\n".join(lines),
            group="tesla-charging",
            icon=config.bark_icon,
            badge=1,
        )
    )


async def send_weekly_report(
    total_trips: int,
    total_distance: float,
    avg_efficiency: float,
    longest_trip: float = 0.0,
    total_duration_min: float = 0.0,
    total_charging_count: int = 0,
    total_energy_added: float = 0.0,
    avg_speed: float = 0.0,
    max_speed: float = 0.0,
) -> bool:
    """发送周报"""

    def format_duration(minutes: float) -> str:
        """格式化时长"""
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        if hours > 0:
            return f"{hours}h {mins}min"
        return f"{mins}min"

    lines = [
        "━━━━━━━━━━━━━━━━",
        "📊 本周驾驶报告（最近7天）",
        "",
        "🚗 行程统计",
        f"  · 行程次数: {total_trips} 次",
        f"  · 行驶里程: {total_distance:.1f} km",
    ]

    # 最长行程
    if longest_trip > 0:
        lines.append(f"  · 最长行程: {longest_trip:.1f} km")

    # 驾驶时长
    if total_duration_min > 0:
        lines.append(f"  · 驾驶时长: {format_duration(total_duration_min)}")

    # 速度统计
    if avg_speed > 0 or max_speed > 0:
        lines.append("")
        lines.append("🚀 速度统计")
        if avg_speed > 0:
            lines.append(f"  · 平均速度: {avg_speed:.1f} km/h")
        if max_speed > 0:
            lines.append(f"  · 最高速度: {max_speed:.1f} km/h")

    # 能耗统计
    lines.extend([
        "",
        "⚡ 能耗统计",
        f"  · 平均能耗: {avg_efficiency:.0f} Wh/km",
    ])

    if total_charging_count > 0:
        lines.extend([
            "",
            "🔌 充电统计",
            f"  · 充电次数: {total_charging_count} 次",
            f"  · 充电总量: {total_energy_added:.1f} kWh",
        ])

    lines.append("━━━━━━━━━━━━━━━━")

    return await send_notification(
        BarkOptions(
            title="🚗 Tesla 周报",
            body="\n".join(lines),
            group="tesla-weekly",
            icon=config.bark_icon,
            badge=1,
        )
    )


async def send_monthly_report(
    total_trips: int,
    total_distance: float,
    avg_efficiency: float,
    longest_trip: float,
    hard_accel_count: int | None = None,
    hard_brake_count: int | None = None,
    driving_grade: str | None = None,
) -> bool:
    """发送月报"""
    lines = [
        "📅 本月驾驶报告",
        "",
        f"🚗 行程次数: {total_trips} 次",
        f"📍 行驶里程: {total_distance:.1f} km",
        f"⚡ 平均能耗: {avg_efficiency:.2f}",
        f"🏆 最长行程: {longest_trip:.1f} km",
    ]

    if hard_accel_count is not None and hard_brake_count is not None and driving_grade is not None:
        lines.append(f"🏁 驾驶评分: 急加速{hard_accel_count}次 · 急减速{hard_brake_count}次（{driving_grade}）")

    return await send_notification(
        BarkOptions(
            title="🚗 Tesla 月报",
            body="\n".join(lines),
            group="tesla-monthly",
            icon=config.bark_icon,
            badge=1,
        )
    )


async def send_daily_briefing(
    location: str,
    weather_condition: str,
    temp: float,
    temp_min: float,
    temp_max: float,
    humidity: int,
    yesterday_trips: int | None = None,
    yesterday_distance: float | None = None,
    yesterday_efficiency: float | None = None,
    suggestion: str | None = None,
) -> bool:
    """发送每日简报"""
    from tesla_notifier.weather import get_weather_icon

    # 根据天气状况获取动态图标
    weather_icon = get_weather_icon(weather_condition)

    lines = [
        "━━━━━━━━━━━━━━━━",
        f"📍 {location}",
        "",
        f"{weather_icon} 今日天气: {weather_condition}",
        f"🌡️ {temp:.1f}°C ({temp_min:.0f}°C ~ {temp_max:.0f}°C)",
        f"💧 湿度 {humidity}%",
    ]

    if yesterday_trips is not None:
        lines.extend([
            "",
            "📊 昨日驾驶",
            f"🚗 {yesterday_trips} 次行程，{yesterday_distance:.1f} km",
            f"⚡ 平均能耗: {yesterday_efficiency:.0f} Wh/km",
        ])

    if suggestion:
        lines.extend(["", "💡 今日建议", suggestion])

    lines.append("━━━━━━━━━━━━━━━━")

    return await send_notification(
        BarkOptions(
            title="☀️ 每日简报",
            body="\n".join(lines),
            group="tesla-daily",
            icon=config.bark_icon,
            badge=1,
        )
    )


async def send_sentry_activated(
    location: str | None = None,
    battery_level: int | None = None,
) -> bool:
    """发送哨兵模式激活推送"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from tesla_notifier.config import config

    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz).strftime("%H:%M")

    lines = [
        "━━━━━━━━━━━━━━━━",
        f"🕐 {now}",
    ]

    if location:
        lines.append(f"📍 {location}")

    if battery_level is not None:
        lines.append(f"🔋 电量: {battery_level}%")

    lines.extend([
        "",
        "哨兵模式已激活，正在监控车辆周围环境",
        "━━━━━━━━━━━━━━━━",
    ])

    return await send_notification(
        BarkOptions(
            title="🛡️ 哨兵模式已激活",
            body="\n".join(lines),
            group="tesla-sentry",
            level="timeSensitive",
            icon=config.bark_icon,
            badge=1,
        )
    )



async def send_sentry_deactivated(
    duration_min: float | None = None,
    battery_level: int | None = None,
) -> bool:
    """发送哨兵模式关闭推送"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from tesla_notifier.config import config

    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz).strftime("%H:%M")

    lines = [
        "━━━━━━━━━━━━━━━━",
        f"🕐 {now}",
    ]

    if duration_min is not None:
        hours = int(duration_min // 60)
        mins = int(duration_min % 60)
        if hours > 0:
            lines.append(f"⏱️ 运行时长: {hours}h {mins}min")
        else:
            lines.append(f"⏱️ 运行时长: {mins}min")

    if battery_level is not None:
        lines.append(f"🔋 电量: {battery_level}%")

    lines.extend([
        "",
        "哨兵模式已关闭",
        "━━━━━━━━━━━━━━━━",
    ])

    return await send_notification(
        BarkOptions(
            title="🛡️ 哨兵模式已关闭",
            body="\n".join(lines),
            group="tesla-sentry",
        )
    )


async def send_sentry_recording(
    power_w: float,
    location: str | None = None,
    battery_level: int | None = None,
) -> bool:
    """发送哨兵录制事件推送

    当哨兵模式下检测到功率跳变（可能有人经过触发录制）时推送通知。

    Args:
        power_w: 触发时的功率（W）
        location: 车辆位置
        battery_level: 电池电量
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from tesla_notifier.config import config

    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz).strftime("%H:%M")

    lines = [
        "━━━━━━━━━━━━━━━━",
        f"🕐 {now}",
    ]

    if location:
        lines.append(f"📍 {location}")

    if battery_level is not None:
        lines.append(f"🔋 电量: {battery_level}%")

    lines.extend([
        "",
        "检测到异常活动，请及时查看车辆状态",
        # f"⚡ 检测到功率跳变: {power_w:.0f}W",
        "",
        "可能有人经过，哨兵正在录制",
        "请及时查看车辆状态",
        "━━━━━━━━━━━━━━━━",
    ])

    return await send_notification(
        BarkOptions(
            title="🎥 哨兵录制中",
            body="\n".join(lines),
            group="tesla-sentry",
            level="timeSensitive",
            sound="minuet",  # 使用不同的提示音区分普通通知
            icon=config.bark_icon,
            badge=1,
        )
    )
