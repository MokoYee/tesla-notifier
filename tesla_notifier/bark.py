"""Bark 推送通知客户端"""

import logging
from dataclasses import dataclass
from typing import Literal

import httpx

from tesla_notifier.config import config
from tesla_notifier.logger import log_with_data, setup_logger

logger = setup_logger("bark")


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
    """发送 Bark 推送"""
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

    except Exception as e:
        logger.exception(f"推送异常: {e}")
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
    hard_accel_pct: float | None = None,
    hard_brake_pct: float | None = None,
    driving_grade: str | None = None,
) -> bool:
    """发送行程结束推送"""
    from datetime import datetime

    def format_time(iso_string: str) -> str:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")

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
        f"📍 {start_address}",
        f"   → {end_address}",
        "",
        f"🕐 {format_time(start_time)} - {format_time(end_time)} ({format_duration(duration)})",
        f"📏 里程: {distance:.1f} km",
        f"⚡ 净能耗: {energy_used:.1f} kWh",
        f"📊 效率: {efficiency:.0f} Wh/km",
        "",
        f"🔋 SoC: {start_soc}% → {end_soc}% ({soc_sign}{soc_diff}%)",
        f"📱 表显: {start_range:.0f} → {end_range:.0f} km",
    ]

    if outside_temp is not None:
        lines.append(f"🌡️ 室外: {outside_temp:.1f}°C")

    if hard_accel_pct is not None and hard_brake_pct is not None and driving_grade is not None:
        lines.append(f"🏁 驾驶评分: {hard_accel_pct:.1f}%急加速 · {hard_brake_pct:.1f}%急减速（{driving_grade}）")

    lines.append("━━━━━━━━━━━━━━━━")

    return await send_notification(
        BarkOptions(title="🚗 行程结束", body="\n".join(lines), group="tesla-trip")
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

    def format_time(iso_string: str) -> str:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")

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
        BarkOptions(title="🔌 充电完成", body="\n".join(lines), group="tesla-charging")
    )


async def send_weekly_report(
    total_trips: int,
    total_distance: float,
    avg_efficiency: float,
) -> bool:
    """发送周报"""
    lines = [
        "📊 本周驾驶报告",
        "",
        f"🚗 行程次数: {total_trips} 次",
        f"📍 行驶里程: {total_distance:.1f} km",
        f"⚡ 平均能耗: {avg_efficiency:.2f}",
    ]
    return await send_notification(
        BarkOptions(title="🚗 Tesla 周报", body="\n".join(lines), group="tesla-weekly")
    )


async def send_monthly_report(
    total_trips: int,
    total_distance: float,
    avg_efficiency: float,
    longest_trip: float,
    hard_accel_pct: float | None = None,
    hard_brake_pct: float | None = None,
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

    if hard_accel_pct is not None and hard_brake_pct is not None and driving_grade is not None:
        lines.append(f"🏁 驾驶评分: {hard_accel_pct:.1f}%急加速 · {hard_brake_pct:.1f}%急减速（{driving_grade}）")

    return await send_notification(
        BarkOptions(title="🚗 Tesla 月报", body="\n".join(lines), group="tesla-monthly")
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
    hard_accel_pct: float | None = None,
    hard_brake_pct: float | None = None,
    driving_grade: str | None = None,
    suggestion: str | None = None,
) -> bool:
    """发送每日简报"""
    lines = [
        "━━━━━━━━━━━━━━━━",
        f"📍 {location}",
        "",
        "🌤️ 今日天气",
        weather_condition,
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
        if hard_accel_pct is not None and hard_brake_pct is not None and driving_grade is not None:
            lines.append(f"🏁 驾驶评分: {hard_accel_pct:.1f}%急加速 · {hard_brake_pct:.1f}%急减速（评分{driving_grade}）")

    if suggestion:
        lines.extend(["", "💡 今日建议", suggestion])

    lines.append("━━━━━━━━━━━━━━━━")

    return await send_notification(
        BarkOptions(title="☀️ 每日简报", body="\n".join(lines), group="tesla-daily")
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
        )
    )


async def send_sentry_event(
    event_type: str,
    location: str | None = None,
    battery_level: int | None = None,
) -> bool:
    """发送哨兵事件推送"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from tesla_notifier.config import config

    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz).strftime("%H:%M")

    # 事件类型映射
    event_titles = {
        "alarm": "🚨 车辆警报触发",
        "sentry": "⚠️ 哨兵事件检测",
        "default": "⚠️ 哨兵事件",
    }
    title = event_titles.get(event_type, event_titles["default"])

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
        "━━━━━━━━━━━━━━━━",
    ])

    return await send_notification(
        BarkOptions(
            title=title,
            body="\n".join(lines),
            group="tesla-sentry",
            level="timeSensitive",
            sound="alarm",
        )
    )


async def send_sentry_deactivated(duration_min: float | None = None) -> bool:
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
