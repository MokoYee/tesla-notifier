"""Bark 推送通知客户端"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import httpx

from tesla_notifier.config import config
from tesla_notifier.health import failure_monitor
from tesla_notifier.logger import log_with_data, setup_logger

logger = setup_logger("bark")

# 重试配置
MAX_RETRIES = 3  # 最大重试次数
RETRY_DELAY = 2  # 重试间隔（秒）

NotificationCertainty = Literal["fact", "analysis", "system"]
NotificationPriority = Literal["high", "medium", "low"]
BarkLevel = Literal["active", "timeSensitive", "passive"]

CERTAINTY_LABELS: dict[NotificationCertainty, str] = {
    "fact": "事实事件",
    "analysis": "分析结果",
    "system": "系统状态",
}
PRIORITY_LABELS: dict[NotificationPriority, str] = {
    "high": "高",
    "medium": "中",
    "low": "低",
}
PRIORITY_TO_LEVEL: dict[NotificationPriority, BarkLevel] = {
    "high": "timeSensitive",
    "medium": "active",
    "low": "passive",
}


@dataclass(frozen=True)
class NotificationMeta:
    """通知元数据。"""

    event_id: str
    certainty: NotificationCertainty
    priority: NotificationPriority
    reason: str | None = None


@dataclass
class BarkOptions:
    """Bark 推送选项"""

    title: str = "Tesla Notifier"
    subtitle: str | None = None
    body: str = ""
    sound: str = "bell"
    icon: str | None = None
    group: str = "tesla"
    url: str | None = None
    badge: int | None = None
    level: BarkLevel | None = None
    meta: NotificationMeta | None = None
    display_meta: bool = False


def _current_local_time() -> str:
    """获取当前本地时间字符串"""
    return datetime.now(ZoneInfo(config.timezone)).strftime("%H:%M")


def _current_local_token() -> str:
    """获取当前本地时间令牌，用于生成事件 ID。"""
    return datetime.now(ZoneInfo(config.timezone)).strftime("%Y%m%d%H%M%S")


def _format_time(iso_string: str) -> str:
    """将 ISO 时间格式化为本地时区时间"""
    dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(config.timezone)).strftime("%H:%M")


def _format_duration(minutes: float) -> str:
    """格式化分钟时长"""
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    if hours > 0:
        return f"{hours}h {mins}min"
    return f"{mins}min"


def _join_lines(lines: list[str]) -> str:
    """合并通知内容，并消除多余空行"""
    normalized: list[str] = []
    previous_blank = False

    for line in lines:
        current_line = line.rstrip()
        is_blank = current_line == ""

        if is_blank and previous_blank:
            continue

        normalized.append(current_line)
        previous_blank = is_blank

    while normalized and normalized[-1] == "":
        normalized.pop()

    return "\n".join(normalized)


def _join_subtitle_parts(*parts: str | None) -> str | None:
    """拼接副标题，避免出现空片段"""
    subtitle_parts = [part.strip() for part in parts if part and part.strip()]
    if not subtitle_parts:
        return None
    return " · ".join(subtitle_parts)


def _normalize_advice_text(advice: str) -> str:
    """归一化建议文案，避免出现“建议 建议 ...”的重复表达。"""
    normalized = advice.strip()
    normalized = re.sub(r"^建议[\s：:,-]*", "", normalized)
    return normalized or advice.strip()


def _normalize_event_part(part: object) -> str:
    """将事件 ID 片段规范化为稳定的短字符串。"""
    normalized = re.sub(r"[^a-z0-9]+", "-", str(part).strip().lower())
    return normalized.strip("-")


def build_event_id(prefix: str, *parts: object) -> str:
    """构造通知事件 ID。"""
    filtered_parts: list[str] = []
    for part in parts:
        if part is None:
            continue
        normalized = _normalize_event_part(part)
        if normalized:
            filtered_parts.append(normalized)
    if not filtered_parts:
        filtered_parts.append(_current_local_token())
    return "-".join([_normalize_event_part(prefix), *filtered_parts])


def _resolve_notification_level(options: BarkOptions) -> BarkLevel:
    """根据通知优先级推导 Bark level。"""
    if options.level is not None:
        return options.level
    if options.meta is None:
        return "active"
    return PRIORITY_TO_LEVEL[options.meta.priority]


def _append_meta_lines(lines: list[str], meta: NotificationMeta) -> None:
    """在通知正文尾部追加元数据块。"""
    lines.extend(
        [
            "",
            "----",
            f"类型 {CERTAINTY_LABELS[meta.certainty]}",
            f"优先级 {PRIORITY_LABELS[meta.priority]}",
            f"事件ID {meta.event_id}",
        ]
    )
    if meta.reason:
        lines.append(f"触发依据 {meta.reason}")


def _compose_notification_body(options: BarkOptions) -> str:
    """合并业务正文与通知元数据。"""
    lines = options.body.splitlines()
    if options.display_meta and options.meta is not None:
        _append_meta_lines(lines, options.meta)
    return _join_lines(lines)


def _build_meta(
    event_id: str,
    certainty: NotificationCertainty,
    priority: NotificationPriority,
    reason: str | None,
) -> NotificationMeta:
    """构造统一通知元数据。"""
    return NotificationMeta(
        event_id=event_id,
        certainty=certainty,
        priority=priority,
        reason=reason,
    )


async def send_notification(options: BarkOptions) -> bool:
    """发送 Bark 推送（带重试机制）

    网络异常时自动重试，最多重试 3 次
    """
    if not config.bark_key:
        logger.warning("BARK_KEY 未配置，跳过推送")
        return False

    url = f"{config.bark_url}/{config.bark_key}"
    body = _compose_notification_body(options)
    level = _resolve_notification_level(options)

    payload: dict[str, object] = {
        "title": options.title,
        "body": body,
        "sound": options.sound,
        "group": options.group,
        "level": level,
    }

    if options.subtitle:
        payload["subtitle"] = options.subtitle
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
        {
            "title": options.title,
            "subtitle": options.subtitle,
            "group": options.group,
            "body_length": len(body),
            "event_id": options.meta.event_id if options.meta else None,
            "priority": options.meta.priority if options.meta else None,
        },
    )
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)

                if response.status_code != 200:
                    failure_monitor.record_bark_failure(
                        f"HTTP {response.status_code}: {response.text[:200]}"
                    )
                    log_with_data(
                        logger,
                        logging.ERROR,
                        f"推送失败: HTTP {response.status_code}",
                        {"status_text": response.text},
                    )
                    return False

                result = response.json()

                if result.get("code") == 200:
                    failure_monitor.record_bark_success()
                    logger.info("推送成功")
                    return True
                else:
                    failure_monitor.record_bark_failure(str(result))
                    log_with_data(logger, logging.ERROR, "推送返回错误", result)
                    return False

        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            # 网络相关异常，进行重试
            failure_monitor.record_bark_failure(str(e))
            if attempt < MAX_RETRIES:
                logger.warning(f"推送失败（第{attempt}次），{RETRY_DELAY}秒后重试: {e}")
                await asyncio.sleep(RETRY_DELAY)
            else:
                logger.error(f"推送失败，已重试{MAX_RETRIES}次: {e}")

        except Exception as e:
            # 其他异常，不重试
            failure_monitor.record_bark_failure(str(e))
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
    driving_score: int | None = None,
    driving_label: str | None = None,
    road_context: str | None = None,
    analysis_summary: str | None = None,
    analysis_advice: str | None = None,
    traffic_label: str | None = None,
    traffic_summary: str | None = None,
    traffic_sample_count: int | None = None,
    speed_avg: float | None = None,
    speed_max: float | None = None,
    odometer: float | None = None,
    trip_id: int | None = None,
) -> bool:
    """发送行程结束推送"""
    soc_diff = end_soc - start_soc
    soc_sign = "+" if soc_diff >= 0 else ""
    subtitle = _join_subtitle_parts(
        f"{distance:.1f} km",
        _format_duration(duration),
    )

    lines = [
        f"📍 {start_address} → {end_address}",
        f"🕐 {_format_time(start_time)} - {_format_time(end_time)}",
        "",
        f"📏 里程 {distance:.1f} km",
    ]

    # 速度信息
    if speed_avg is not None and speed_max is not None:
        lines.append(f"🚀 均速 {speed_avg:.1f} km/h · 最高 {speed_max:.1f} km/h")

    # 能耗信息（只有在有效时才显示）
    if energy_used and energy_used > 0:
        lines.extend([
            f"⚡ 净能耗 {energy_used:.1f} kWh",
            f"📊 效率 {efficiency:.0f} Wh/km",
            "",
        ])
    else:
        lines.append("")

    if outside_temp is not None:
        lines.append(f"🌡️ 室外 {outside_temp:.1f}°C")

    range_diff = start_range - end_range
    range_sign = "-" if range_diff > 0 else "+"
    lines.extend([
        f"🔋 SoC {start_soc}% → {end_soc}%（{soc_sign}{soc_diff}%）",
        f"📟 表显 {start_range:.0f} → {end_range:.0f} km（{range_sign}{abs(range_diff):.0f} km）",
    ])

    # 计算续航达成率
    range_consumed = float(start_range) - float(end_range)  # 表显消耗的续航
    if range_consumed > 0 and distance > 0:
        range_achievement_rate = (float(distance) / range_consumed) * 100
        lines.append(f"🎯 续航达成率 {range_achievement_rate:.1f}%")

    # 总里程
    if odometer is not None:
        lines.append(f"🛣️ 总里程 {odometer:.1f} km")

    if (
        hard_accel_count is not None
        and hard_brake_count is not None
        and driving_score is not None
        and driving_label is not None
    ):
        lines.append("")
        lines.append(f"🏁 驾驶评分 {driving_score} 分 · {driving_label}")
        detail_line = f"急加速{hard_accel_count}次 · 急减速{hard_brake_count}次"
        if road_context:
            detail_line = f"{road_context} · {detail_line}"
        lines.append(f"🧭 {detail_line}")

    if traffic_label:
        traffic_line = traffic_label
        if traffic_sample_count:
            traffic_line = f"{traffic_line} · 采样{traffic_sample_count}次"
        lines.append(f"🚦 路况 · {traffic_line}")

    if traffic_summary:
        lines.append(f"🗺️ 交通 · {traffic_summary}")

    if analysis_summary:
        lines.append(f"🧠 分析 · {analysis_summary}")

    if analysis_advice:
        lines.append(f"💡 建议 · {_normalize_advice_text(analysis_advice)}")

    return await send_notification(
        BarkOptions(
            title="🚗 行程结束",
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-trip",
            icon=config.bark_icon,
            badge=1,
            meta=_build_meta(
                event_id=build_event_id("trip-end", trip_id),
                certainty="fact",
                priority="medium",
                reason=(
                    "TeslaMate 已确认行程结束并完成数据库落盘；"
                    "评分、建议与路况摘要属于附带分析结果"
                ),
            ),
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
    charging_id: int | None = None,
) -> bool:
    """发送充电完成推送"""
    subtitle = _join_subtitle_parts(
        f"{start_soc}% → {end_soc}%",
        f"{energy_added:.1f} kWh",
    )
    lines = [
        f"📍 {location}",
        f"🕐 {_format_time(start_time)} - {_format_time(end_time)}（{_format_duration(duration)}）",
        "",
        f"🔋 SoC {start_soc}% → {end_soc}%（+{end_soc - start_soc}%）",
        f"⚡ 充入 {energy_added:.1f} kWh",
        f"📊 峰值功率 {peak_power:.0f} kW",
        f"📱 表显 {start_range:.0f} → {end_range:.0f} km",
    ]

    if outside_temp is not None:
        lines.append(f"🌡️ 室外 {outside_temp:.1f}°C")

    if cost is not None:
        lines.append(f"💰 费用 ¥{cost:.2f}")

    return await send_notification(
        BarkOptions(
            title="🔋 充电完成",
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-charging",
            icon=config.bark_icon,
            badge=1,
            meta=_build_meta(
                event_id=build_event_id("charging-complete", charging_id),
                certainty="fact",
                priority="medium",
                reason="TeslaMate charging_state 已结束且充电记录已写入数据库",
            ),
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
    period_tag: str | None = None,
) -> bool:
    """发送周报"""
    subtitle = _join_subtitle_parts(
        f"{total_trips}次行程",
        f"{total_distance:.1f} km",
    )
    lines = [
        "【行程】",
        f"• 行程次数 {total_trips} 次",
        f"• 行驶里程 {total_distance:.1f} km",
    ]

    # 最长行程
    if longest_trip > 0:
        lines.append(f"• 最长行程 {longest_trip:.1f} km")

    # 驾驶时长
    if total_duration_min > 0:
        lines.append(f"• 驾驶时长 {_format_duration(total_duration_min)}")

    # 速度统计
    if avg_speed > 0 or max_speed > 0:
        lines.append("")
        lines.append("【速度】")
        if avg_speed > 0:
            lines.append(f"• 平均速度 {avg_speed:.1f} km/h")
        if max_speed > 0:
            lines.append(f"• 最高速度 {max_speed:.1f} km/h")

    # 能耗统计
    lines.extend([
        "",
        "【能耗】",
        f"• 平均能耗 {avg_efficiency:.0f} Wh/km",
    ])

    if total_charging_count > 0:
        lines.extend([
            "",
            "【充电】",
            f"• 充电次数 {total_charging_count} 次",
            f"• 充电总量 {total_energy_added:.1f} kWh",
        ])

    return await send_notification(
        BarkOptions(
            title="🚗 Tesla 周报",
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-weekly",
            icon=config.bark_icon,
            badge=1,
            meta=_build_meta(
                event_id=build_event_id("weekly-report", period_tag or _current_local_token()),
                certainty="analysis",
                priority="low",
                reason="基于 TeslaMate 最近 7 天行程与充电数据聚合生成",
            ),
        )
    )


async def send_monthly_report(
    total_trips: int,
    total_distance: float,
    avg_efficiency: float,
    longest_trip: float,
    period_tag: str | None = None,
) -> bool:
    """发送月报"""
    subtitle = _join_subtitle_parts(
        f"{total_trips}次行程",
        f"{total_distance:.1f} km",
    )
    lines = [
        f"🚗 行程次数 {total_trips} 次",
        f"📍 行驶里程 {total_distance:.1f} km",
        f"⚡ 平均能耗 {avg_efficiency:.1f} Wh/km",
        f"🏆 最长行程 {longest_trip:.1f} km",
    ]

    return await send_notification(
        BarkOptions(
            title="🚗 Tesla 月报",
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-monthly",
            icon=config.bark_icon,
            badge=1,
            meta=_build_meta(
                event_id=build_event_id("monthly-report", period_tag or _current_local_token()),
                certainty="analysis",
                priority="low",
                reason="基于 TeslaMate 上个自然月数据聚合生成",
            ),
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
    period_tag: str | None = None,
) -> bool:
    """发送每日简报"""
    from tesla_notifier.weather import get_weather_icon

    # 根据天气状况获取动态图标
    weather_icon = get_weather_icon(weather_condition)

    subtitle = _join_subtitle_parts(
        weather_condition,
        f"{temp:.1f}°C",
    )
    lines = [
        f"📍 {location}",
        "",
        f"{weather_icon} 今日天气 {weather_condition}",
        f"🌡️ 当前 {temp:.1f}°C（{temp_min:.0f}°C ~ {temp_max:.0f}°C）",
        f"💧 湿度 {humidity}%",
    ]

    if yesterday_trips is not None:
        lines.extend([
            "",
            "【昨日驾驶】",
            f"🚗 {yesterday_trips} 次行程，{yesterday_distance:.1f} km",
            f"⚡ 平均能耗 {yesterday_efficiency:.0f} Wh/km",
        ])

    if suggestion:
        lines.extend(["", "【今日建议】", suggestion])

    return await send_notification(
        BarkOptions(
            title="☀️ 每日简报",
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-daily",
            icon=config.bark_icon,
            badge=1,
            meta=_build_meta(
                event_id=build_event_id("daily-briefing", period_tag or _current_local_token()),
                certainty="analysis",
                priority="low",
                reason="天气数据与昨日驾驶汇总的组合分析结果",
            ),
        )
    )


async def send_sentry_activated(
    location: str | None = None,
    battery_level: int | None = None,
    session_tag: str | None = None,
) -> bool:
    """发送哨兵模式激活推送"""
    subtitle = "离车守护中"
    lines = [
        f"🕐 {_current_local_time()}",
    ]

    if location:
        lines.append(f"📍 {location}")

    if battery_level is not None:
        lines.append(f"🔋 电量 {battery_level}%")

    lines.extend([
        "",
        "已进入哨兵模式",
        "离车期间如有异常活动，将立即推送提醒",
    ])

    return await send_notification(
        BarkOptions(
            title="🛡️ 哨兵已开启",
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-sentry",
            icon=config.bark_icon,
            badge=1,
            meta=_build_meta(
                event_id=build_event_id("sentry-activated", session_tag or _current_local_token()),
                certainty="fact",
                priority="medium",
                reason="TeslaMate MQTT sentry_mode 状态切换为 true",
            ),
        )
    )


async def send_sentry_deactivated(
    location: str | None = None,
    duration_min: float | None = None,
    battery_level: int | None = None,
    battery_drop: int | None = None,
    recording_count: int = 0,
    session_tag: str | None = None,
) -> bool:
    """发送哨兵模式关闭推送"""
    subtitle = "本次监控结束"
    lines = [
        f"🕐 {_current_local_time()}",
    ]

    if location:
        lines.append(f"📍 {location}")

    if battery_level is not None:
        lines.append(f"🔋 结束电量 {battery_level}%")

    lines.extend([
        "",
        "哨兵模式已关闭",
    ])

    if duration_min is not None:
        hours = int(duration_min // 60)
        mins = int(duration_min % 60)
        if hours > 0:
            lines.append(f"⏱️ 运行时长 {hours}h {mins}min")
        else:
            lines.append(f"⏱️ 运行时长 {mins}min")

    if battery_drop is not None:
        lines.append(f"📉 本次耗电 {battery_drop}%")

    if recording_count > 0:
        lines.append(f"🎥 触发录制 {recording_count} 次")

    return await send_notification(
        BarkOptions(
            title="🛡️ 哨兵已关闭",
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-sentry",
            meta=_build_meta(
                event_id=build_event_id(
                    "sentry-deactivated",
                    session_tag or _current_local_token(),
                ),
                certainty="fact",
                priority="medium",
                reason="TeslaMate MQTT sentry_mode 状态切换为 false",
            ),
        )
    )


async def send_sentry_recording(
    location: str | None = None,
    battery_level: int | None = None,
    recording_count: int = 0,
    session_tag: str | None = None,
) -> bool:
    """发送哨兵录制事件推送

    当检测到车辆进入哨兵录制状态时推送通知。
    """
    subtitle = f"第 {recording_count} 次录制" if recording_count > 0 else "检测到活动"
    lines = [
        f"🕐 {_current_local_time()}",
    ]

    if location:
        lines.append(f"📍 {location}")

    if battery_level is not None:
        lines.append(f"🔋 电量 {battery_level}%")

    lines.extend([
        "",
        "检测到异常活动",
        "车辆已开始录制，请及时查看 Tesla App",
    ])

    if recording_count > 0:
        lines.append(f"🎥 本次第 {recording_count} 次录制")

    return await send_notification(
        BarkOptions(
            title="🎥 哨兵检测到活动",
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-sentry",
            sound="minuet",  # 使用不同的提示音区分普通通知
            icon=config.bark_icon,
            badge=1,
            meta=_build_meta(
                event_id=build_event_id(
                    "sentry-recording",
                    session_tag or _current_local_token(),
                    recording_count or None,
                ),
                certainty="fact",
                priority="high",
                reason="TeslaMate MQTT center_display_state=7，表示哨兵录制已触发",
            ),
        )
    )


async def send_departure_safety_alert(
    issues: list[str],
    location: str | None = None,
    battery_level: int | None = None,
    session_tag: str | None = None,
) -> bool:
    """发送离车安全提醒"""
    if len(issues) <= 2:
        subtitle = "、".join(issues)
    else:
        subtitle = f"{'、'.join(issues[:2])} 等{len(issues)}项"
    lines = [
        f"🕐 {_current_local_time()}",
    ]

    if location:
        lines.append(f"📍 {location}")

    if battery_level is not None:
        lines.append(f"🔋 电量 {battery_level}%")

    lines.extend([
        "",
        "离车后发现以下风险：",
    ])

    for issue in issues:
        lines.append(f"• {issue}")

    lines.append("")
    lines.append("请及时确认车辆状态")

    return await send_notification(
        BarkOptions(
            title="🚨 离车安全提醒",
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-safety",
            icon=config.bark_icon,
            badge=1,
            meta=_build_meta(
                event_id=build_event_id(
                    "departure-safety",
                    session_tag or _current_local_token(),
                    len(issues),
                ),
                certainty="analysis",
                priority="high",
                reason="离车延迟校验后，门窗/锁车/充电口等状态组合规则命中风险",
            ),
        )
    )


async def send_tire_pressure_alert(
    warning_wheels: list[str],
    pressures: dict[str, float | None],
    location: str | None = None,
    session_tag: str | None = None,
) -> bool:
    """发送胎压异常提醒"""
    if len(warning_wheels) == 1:
        subtitle = f"{warning_wheels[0]}异常"
    elif len(warning_wheels) == 2:
        subtitle = f"{warning_wheels[0]}、{warning_wheels[1]}异常"
    else:
        subtitle = f"{'、'.join(warning_wheels[:2])}等{len(warning_wheels)}轮异常"
    lines = [
        f"🕐 {_current_local_time()}",
    ]

    if location:
        lines.append(f"📍 {location}")

    lines.extend([
        "",
        "当前胎压：",
    ])

    for wheel, pressure in pressures.items():
        suffix = " ⚠️" if wheel in warning_wheels else ""
        if pressure is None:
            lines.append(f"• {wheel}: 无数据{suffix}")
        else:
            lines.append(f"• {wheel}: {pressure:.1f} bar{suffix}")

    lines.append("")
    lines.append("建议尽快检查轮胎状态")

    return await send_notification(
        BarkOptions(
            title="🛞 胎压异常",
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-tpms",
            icon=config.bark_icon,
            badge=1,
            meta=_build_meta(
                event_id=build_event_id(
                    "tpms-alert",
                    session_tag or _current_local_token(),
                    len(warning_wheels),
                ),
                certainty="fact",
                priority="high",
                reason="TeslaMate MQTT tpms_soft_warning_* 实时告警为 true",
            ),
        )
    )


async def send_charging_issue_alert(
    issue_type: str,
    location: str | None = None,
    battery_level: int | None = None,
    charge_limit_soc: int | None = None,
    charger_power: float | None = None,
    plugged_in: bool | None = None,
    session_tag: str | None = None,
) -> bool:
    """发送充电异常提醒"""
    if issue_type == "no_power":
        title = "⚡ 充电电源异常"
        summary = "车辆已连接，但当前未获取到供电"
        subtitle = "当前无供电"
    else:
        title = "⚠️ 充电意外停止"
        summary = "当前电量未达到设定上限，充电提前结束"
        subtitle = "未达到设定上限"

    lines = [
        f"🕐 {_current_local_time()}",
    ]

    if location:
        lines.append(f"📍 {location}")

    if battery_level is not None:
        lines.append(f"🔋 当前电量 {battery_level}%")

    lines.extend([
        "",
        summary,
    ])

    if charge_limit_soc is not None:
        lines.append(f"🎯 充电上限 {charge_limit_soc}%")

    if charger_power is not None:
        lines.append(f"🔌 当前功率 {charger_power:.1f} kW")

    if plugged_in is not None:
        plugged_in_text = "已连接" if plugged_in else "未连接"
        lines.append(f"🔗 充电连接 {plugged_in_text}")

    lines.append("")
    lines.append("建议检查电源、充电桩或车辆状态")

    return await send_notification(
        BarkOptions(
            title=title,
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-charging",
            icon=config.bark_icon,
            badge=1,
            meta=_build_meta(
                event_id=build_event_id(
                    "charging-issue",
                    issue_type,
                    session_tag or _current_local_token(),
                ),
                certainty="fact",
                priority="high",
                reason=(
                    "TeslaMate MQTT charging_state 命中异常状态，"
                    "例如 NoPower 或 Stopped 且 SoC 未达到目标"
                ),
            ),
        )
    )


async def send_system_status(
    title: str,
    subtitle: str | None,
    lines: list[str],
    event_id: str,
    priority: NotificationPriority,
    reason: str,
) -> bool:
    """发送系统状态类通知。"""
    return await send_notification(
        BarkOptions(
            title=title,
            subtitle=subtitle,
            body=_join_lines(lines),
            group="tesla-system",
            icon=config.bark_icon,
            badge=1,
            display_meta=True,
            meta=_build_meta(
                event_id=event_id,
                certainty="system",
                priority=priority,
                reason=reason,
            ),
        )
    )
