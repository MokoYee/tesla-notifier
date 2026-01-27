"""数据库查询模块"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

import psycopg
from psycopg_pool import AsyncConnectionPool

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("database")

# 全局连接池（延迟初始化）
_pool: AsyncConnectionPool | None = None


def _format_utc_time(dt: datetime | None) -> str:
    """将数据库返回的 naive datetime 转换为 UTC ISO 格式字符串

    TeslaMate 数据库存储的 timestamp 类型没有时区信息，但实际是 UTC 时间。
    需要手动添加 UTC 时区标记，以便后续正确转换到本地时区。

    Args:
        dt: 数据库返回的 datetime 对象（无时区信息）

    Returns:
        ISO 格式字符串，带 +00:00 时区标记，如 "2026-01-16T00:08:47.893000+00:00"
    """
    if not dt:
        return ""
    # 将 naive datetime 标记为 UTC 时区
    utc_dt = dt.replace(tzinfo=timezone.utc)
    return utc_dt.isoformat()


def _local_to_utc(local_dt: datetime) -> datetime:
    """将本地时区的 datetime 转换为 UTC naive datetime（用于数据库查询）

    TeslaMate 数据库存储的是 UTC 时间（无时区信息），所以查询时需要将本地时间转换为 UTC。

    Args:
        local_dt: 带时区信息的本地 datetime

    Returns:
        UTC 时间的 naive datetime（无时区信息），可直接用于数据库查询
    """
    # 转换为 UTC 时间
    utc_dt = local_dt.astimezone(timezone.utc)
    # 移除时区信息，返回 naive datetime
    return utc_dt.replace(tzinfo=None)


def _get_local_date_range(date_str: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """获取某个本地日期对应的 UTC 时间范围（用于数据库查询）

    例如：本地日期 "2026-01-17" (Asia/Shanghai) 对应的 UTC 时间范围是
    2026-01-16 16:00:00 到 2026-01-17 16:00:00

    Args:
        date_str: 本地日期字符串，格式 "YYYY-MM-DD"
        tz: 时区信息

    Returns:
        (start_utc, end_utc) 元组，都是 naive datetime（无时区信息）
    """
    # 解析本地日期（当天 00:00:00）
    local_date = datetime.strptime(date_str, "%Y-%m-%d")
    local_start = local_date.replace(tzinfo=tz)
    # 当天结束时间（次日 00:00:00）
    local_end = local_start + timedelta(days=1)

    # 转换为 UTC naive datetime
    return _local_to_utc(local_start), _local_to_utc(local_end)


@dataclass
class TripData:
    """行程数据"""

    id: int
    car_id: int
    start_date: str
    end_date: str
    start_address: str
    end_address: str
    distance: float
    duration_min: float
    start_rated_range_km: float
    end_rated_range_km: float
    start_battery_level: int
    end_battery_level: int
    outside_temp_avg: float | None
    speed_max: float | None
    speed_avg: float | None
    odometer: float | None  # 总里程


@dataclass
class ChargingData:
    """充电数据"""

    id: int
    car_id: int
    start_date: str
    end_date: str
    location: str
    duration_min: float
    start_battery_level: int
    end_battery_level: int
    charge_energy_added: float
    charger_power_max: float
    start_rated_range_km: float
    end_rated_range_km: float
    outside_temp_avg: float | None


@dataclass
class DrivingSummary:
    """驾驶汇总"""

    total_trips: int
    total_distance: float
    avg_efficiency: float
    longest_trip: float = 0.0
    total_duration_min: float = 0.0  # 总驾驶时长（分钟）
    total_charging_count: int = 0  # 充电次数
    total_energy_added: float = 0.0  # 总充电量（kWh）
    avg_speed: float = 0.0  # 平均速度（km/h）
    max_speed: float = 0.0  # 最高速度（km/h）


@dataclass
class DrivingScore:
    """驾驶评分"""

    hard_accel_count: int  # 急加速次数 (power >= 100kW)
    hard_brake_count: int  # 急减速次数 (power <= -55kW)
    grade: str  # 评分等级 A/B/C/D


async def init_pool() -> None:
    """初始化数据库连接池

    应在应用启动时调用一次。
    """
    global _pool
    if _pool is not None:
        return

    logger.info("正在初始化数据库连接池...")
    _pool = AsyncConnectionPool(
        conninfo=config.db_dsn,
        min_size=1,  # 最小连接数
        max_size=5,  # 最大连接数
        open=False,  # 显式打开
    )
    await _pool.open()
    logger.info("数据库连接池已初始化")


async def close_pool() -> None:
    """关闭数据库连接池

    应在应用关闭时调用。
    """
    global _pool
    if _pool is not None:
        logger.info("正在关闭数据库连接池...")
        await _pool.close()
        _pool = None
        logger.info("数据库连接池已关闭")


@asynccontextmanager
async def get_connection() -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """获取数据库连接（从连接池）

    使用方式：
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(...)
    """
    global _pool

    # 如果池未初始化，自动初始化（兼容旧代码）
    if _pool is None:
        await init_pool()

    async with _pool.connection() as conn:  # type: ignore[union-attr]
        yield conn


async def resolve_location_name(
    conn: psycopg.AsyncConnection[Any],
    geofence_id: int | None,
    position_id: int | None,
    address_name: str | None,
) -> str:
    """解析位置名称

    优先级：
    1. geofence_id 关联的收藏地点名称
    2. 根据 position_id 的经纬度实时匹配 geofence（在半径范围内）
    3. 通过高德地图 API 获取中文地址（如果配置了 AMAP_KEY）
    4. 地址名称 address_name（兜底）

    Args:
        conn: 数据库连接
        geofence_id: 收藏地点 ID（可能为 None）
        position_id: 位置点 ID（用于获取经纬度）
        address_name: 地址名称（兜底）

    Returns:
        解析后的位置名称
    """
    from tesla_notifier import amap

    async with conn.cursor() as cur:
        # 1. 优先使用已关联的 geofence 名称
        if geofence_id:
            await cur.execute(
                "SELECT name FROM geofences WHERE id = %s",
                (geofence_id,),
            )
            row = await cur.fetchone()
            if row and row[0]:
                return row[0]

        # 2. 根据 position_id 的经纬度实时匹配 geofence
        latitude: float | None = None
        longitude: float | None = None

        if position_id:
            await cur.execute(
                """
                SELECT g.name, p.latitude, p.longitude
                FROM positions p
                LEFT JOIN geofences g ON earth_box(ll_to_earth(g.latitude, g.longitude), g.radius)
                    @> ll_to_earth(p.latitude, p.longitude)
                    AND earth_distance(ll_to_earth(g.latitude, g.longitude),
                                       ll_to_earth(p.latitude, p.longitude)) < g.radius
                WHERE p.id = %s
                ORDER BY earth_distance(ll_to_earth(g.latitude, g.longitude),
                                        ll_to_earth(p.latitude, p.longitude)) ASC
                LIMIT 1
                """,
                (position_id,),
            )
            row = await cur.fetchone()
            if row:
                # 如果匹配到 geofence，直接返回
                if row[0]:
                    return row[0]
                # 保存坐标用于高德 API 查询
                latitude = float(row[1]) if row[1] else None
                longitude = float(row[2]) if row[2] else None

        # 3. 通过高德地图 API 获取中文地址
        if latitude and longitude:
            amap_address = await amap.reverse_geocode(latitude, longitude)
            if amap_address:
                return amap_address

        # 4. 使用原始地址名称（兜底）
        return address_name or "未知地点"


async def get_latest_trip(car_id: str) -> TripData | None:
    """获取最新行程"""
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        d.id,
                        d.car_id,
                        d.start_date,
                        d.end_date,
                        d.distance,
                        d.duration_min,
                        d.start_rated_range_km,
                        d.end_rated_range_km,
                        sp.battery_level as start_battery_level,
                        ep.battery_level as end_battery_level,
                        d.outside_temp_avg,
                        d.speed_max,
                        d.start_geofence_id,
                        d.end_geofence_id,
                        d.start_position_id,
                        d.end_position_id,
                        sa.name as start_address_name,
                        ea.name as end_address_name,
                        CASE WHEN d.duration_min > 0
                             THEN d.distance / (d.duration_min / 60.0)
                             ELSE NULL END as speed_avg,
                        ep.odometer as odometer
                    FROM drives d
                    LEFT JOIN addresses sa ON d.start_address_id = sa.id
                    LEFT JOIN addresses ea ON d.end_address_id = ea.id
                    LEFT JOIN positions sp ON d.start_position_id = sp.id
                    LEFT JOIN positions ep ON d.end_position_id = ep.id
                    WHERE d.car_id = %s AND d.end_date IS NOT NULL
                    ORDER BY d.end_date DESC
                    LIMIT 1
                    """,
                    (car_id,),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                # 解析起点和终点位置名称
                start_address = await resolve_location_name(
                    conn, row[12], row[14], row[16]
                )
                end_address = await resolve_location_name(
                    conn, row[13], row[15], row[17]
                )

                return TripData(
                    id=row[0],
                    car_id=row[1],
                    start_date=_format_utc_time(row[2]),
                    end_date=_format_utc_time(row[3]),
                    start_address=start_address,
                    end_address=end_address,
                    distance=float(row[4] or 0),
                    duration_min=float(row[5] or 0),
                    start_rated_range_km=float(row[6] or 0),
                    end_rated_range_km=float(row[7] or 0),
                    start_battery_level=int(row[8] or 0),
                    end_battery_level=int(row[9] or 0),
                    outside_temp_avg=float(row[10]) if row[10] else None,
                    speed_max=float(row[11]) if row[11] else None,
                    speed_avg=float(row[18]) if row[18] else None,
                    odometer=float(row[19]) if row[19] else None,
                )
    except Exception as e:
        logger.exception(f"查询最新行程失败: {e}")
        return None


async def get_latest_charging(car_id: str) -> ChargingData | None:
    """获取最新充电记录"""
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        c.id,
                        c.car_id,
                        c.start_date,
                        c.end_date,
                        c.duration_min,
                        c.start_battery_level,
                        c.end_battery_level,
                        c.charge_energy_added,
                        c.start_rated_range_km,
                        c.end_rated_range_km,
                        c.outside_temp_avg,
                        c.geofence_id,
                        c.position_id,
                        a.name as address_name
                    FROM charging_processes c
                    LEFT JOIN addresses a ON c.address_id = a.id
                    WHERE c.car_id = %s AND c.end_date IS NOT NULL
                    ORDER BY c.end_date DESC
                    LIMIT 1
                    """,
                    (car_id,),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                charging_id = row[0]

                # 从 charges 表查询该充电过程的最大功率
                await cur.execute(
                    """
                    SELECT MAX(charger_power) as max_power
                    FROM charges
                    WHERE charging_process_id = %s
                    AND charger_power IS NOT NULL
                    """,
                    (charging_id,),
                )
                power_row = await cur.fetchone()
                charger_power_max = float(power_row[0]) if power_row and power_row[0] else 0.0

                # 解析充电位置名称
                location = await resolve_location_name(
                    conn, row[11], row[12], row[13]
                )

                return ChargingData(
                    id=charging_id,
                    car_id=row[1],
                    start_date=_format_utc_time(row[2]),
                    end_date=_format_utc_time(row[3]),
                    location=location,
                    duration_min=float(row[4] or 0),
                    start_battery_level=int(row[5] or 0),
                    end_battery_level=int(row[6] or 0),
                    charge_energy_added=float(row[7] or 0),
                    charger_power_max=charger_power_max,
                    start_rated_range_km=float(row[8] or 0),
                    end_rated_range_km=float(row[9] or 0),
                    outside_temp_avg=float(row[10]) if row[10] else None,
                )
    except Exception as e:
        logger.exception(f"查询最新充电记录失败: {e}")
        return None


async def get_yesterday_summary(car_id: str) -> DrivingSummary | None:
    """获取昨日驾驶汇总"""
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                tz = ZoneInfo(config.timezone)
                yesterday = datetime.now(tz) - timedelta(days=1)
                date_str = yesterday.strftime("%Y-%m-%d")

                # 获取昨日本地时间对应的 UTC 时间范围
                start_utc, end_utc = _get_local_date_range(date_str, tz)

                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as trips,
                        COALESCE(SUM(d.distance), 0) as total_distance,
                        COALESCE(SUM(GREATEST(d.start_rated_range_km - d.end_rated_range_km, 0)), 0) as rated_range_used,
                        c.efficiency * 1000 as car_efficiency_wh_km
                    FROM drives d
                    JOIN cars c ON d.car_id = c.id
                    WHERE d.car_id = %s
                        AND d.start_date >= %s
                        AND d.start_date < %s
                        AND d.end_date IS NOT NULL
                    GROUP BY c.efficiency
                    """,
                    (car_id, start_utc, end_utc),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                trips = int(row[0] or 0)
                distance = float(row[1] or 0)
                rated_range_used = float(row[2] or 0)
                car_efficiency = float(row[3] or 150.0)  # 单位: Wh/km，默认 150 Wh/km

                if trips == 0:
                    return None

                # 计算效率 (Wh/km)，使用车辆动态 efficiency 值
                efficiency = (rated_range_used * car_efficiency) / distance if distance > 0 else 0

                return DrivingSummary(
                    total_trips=trips,
                    total_distance=distance,
                    avg_efficiency=efficiency,
                )
    except Exception as e:
        logger.exception(f"查询昨日汇总失败: {e}")
        return None


async def get_weekly_summary(car_id: str) -> DrivingSummary | None:
    """获取周驾驶汇总（最近7天）"""
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                tz = ZoneInfo(config.timezone)
                # 本地时间的当前时刻和7天前
                local_end = datetime.now(tz)
                local_start = local_end - timedelta(days=7)

                # 转换为 UTC naive datetime（用于数据库查询）
                start_utc = _local_to_utc(local_start)
                end_utc = _local_to_utc(local_end)

                # 查询行程统计
                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as trips,
                        COALESCE(SUM(d.distance), 0) as total_distance,
                        COALESCE(SUM(GREATEST(d.start_rated_range_km - d.end_rated_range_km, 0)), 0) as rated_range_used,
                        COALESCE(MAX(d.distance), 0) as longest_trip,
                        COALESCE(SUM(d.duration_min), 0) as total_duration_min,
                        COALESCE(AVG(d.speed_max), 0) as max_speed,
                        c.efficiency * 1000 as car_efficiency_wh_km
                    FROM drives d
                    JOIN cars c ON d.car_id = c.id
                    WHERE d.car_id = %s
                        AND d.start_date >= %s
                        AND d.start_date < %s
                        AND d.end_date IS NOT NULL
                    GROUP BY c.efficiency
                    """,
                    (car_id, start_utc, end_utc),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                trips = int(row[0] or 0)
                distance = float(row[1] or 0)
                rated_range_used = float(row[2] or 0)
                longest_trip = float(row[3] or 0)
                total_duration_min = float(row[4] or 0)
                max_speed = float(row[5] or 0)
                car_efficiency = float(row[6] or 150.0)  # 单位: Wh/km，默认 150 Wh/km

                if trips == 0:
                    return None

                # 计算效率 (Wh/km)，使用车辆动态 efficiency 值
                efficiency = (rated_range_used * car_efficiency) / distance if distance > 0 else 0

                # 计算平均速度（km/h）
                avg_speed = (distance / (total_duration_min / 60.0)) if total_duration_min > 0 else 0

                # 查询充电统计
                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as charging_count,
                        COALESCE(SUM(charge_energy_added), 0) as total_energy_added
                    FROM charging_processes
                    WHERE car_id = %s
                        AND start_date >= %s
                        AND start_date < %s
                        AND end_date IS NOT NULL
                    """,
                    (car_id, start_utc, end_utc),
                )
                charging_row = await cur.fetchone()

                charging_count = int(charging_row[0] or 0) if charging_row else 0
                energy_added = float(charging_row[1] or 0) if charging_row else 0

                return DrivingSummary(
                    total_trips=trips,
                    total_distance=distance,
                    avg_efficiency=efficiency,
                    longest_trip=longest_trip,
                    total_duration_min=total_duration_min,
                    total_charging_count=charging_count,
                    total_energy_added=energy_added,
                    avg_speed=avg_speed,
                    max_speed=max_speed,
                )
    except Exception as e:
        logger.exception(f"查询周汇总失败: {e}")
        return None


async def get_monthly_summary(car_id: str) -> DrivingSummary | None:
    """获取月驾驶汇总（上个自然月）"""
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                tz = ZoneInfo(config.timezone)
                now = datetime.now(tz)

                # 计算上个月的起止时间（本地时区）
                # 本月1号 00:00:00
                local_end = datetime(now.year, now.month, 1, tzinfo=tz)
                # 上个月1号 00:00:00
                if now.month == 1:
                    local_start = datetime(now.year - 1, 12, 1, tzinfo=tz)
                else:
                    local_start = datetime(now.year, now.month - 1, 1, tzinfo=tz)

                # 转换为 UTC naive datetime（用于数据库查询）
                start_utc = _local_to_utc(local_start)
                end_utc = _local_to_utc(local_end)

                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as trips,
                        COALESCE(SUM(d.distance), 0) as total_distance,
                        COALESCE(SUM(GREATEST(d.start_rated_range_km - d.end_rated_range_km, 0)), 0) as rated_range_used,
                        COALESCE(MAX(d.distance), 0) as longest_trip,
                        c.efficiency * 1000 as car_efficiency_wh_km
                    FROM drives d
                    JOIN cars c ON d.car_id = c.id
                    WHERE d.car_id = %s
                        AND d.start_date >= %s
                        AND d.start_date < %s
                        AND d.end_date IS NOT NULL
                    GROUP BY c.efficiency
                    """,
                    (car_id, start_utc, end_utc),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                trips = int(row[0] or 0)
                distance = float(row[1] or 0)
                rated_range_used = float(row[2] or 0)
                longest_trip = float(row[3] or 0)
                car_efficiency = float(row[4] or 150.0)  # 单位: Wh/km，默认 150 Wh/km

                if trips == 0:
                    return None

                # 计算效率 (Wh/km)，使用车辆动态 efficiency 值
                efficiency = (rated_range_used * car_efficiency) / distance if distance > 0 else 0

                return DrivingSummary(
                    total_trips=trips,
                    total_distance=distance,
                    avg_efficiency=efficiency,
                    longest_trip=longest_trip,
                )
    except Exception as e:
        logger.exception(f"查询月汇总失败: {e}")
        return None

async def get_car_efficiency(car_id: str) -> float:
    """获取车辆的 efficiency 值（Wh/km）

    TeslaMate 数据库存储的 efficiency 单位是 kWh/km，
    需要乘以 1000 转换为 Wh/km。
    如果查询失败或值为空，返回默认值 150.0 Wh/km。
    """
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT efficiency FROM cars WHERE id = %s",
                    (car_id,),
                )
                row = await cur.fetchone()

                if row and row[0]:
                    # 数据库单位是 kWh/km，转换为 Wh/km
                    return float(row[0]) * 1000.0
                return 150.0  # 默认值
    except Exception as e:
        logger.exception(f"查询车辆 efficiency 失败: {e}")
        return 150.0


async def get_vehicle_last_position(car_id: str) -> tuple[int, float, float] | None:
    """获取车辆最后位置

    Returns:
        (position_id, latitude, longitude) 或 None
    """
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        latitude,
                        longitude
                    FROM positions
                    WHERE car_id = %s
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    (car_id,),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                return (int(row[0]), float(row[1]), float(row[2]))
    except Exception as e:
        logger.exception(f"查询车辆位置失败: {e}")
        return None


def _calculate_grade(hard_accel_count: int, hard_brake_count: int) -> str:
    """根据急加速和急减速次数计算评分等级

    评分规则:
        A: 急加速<5次 且 急减速<3次
        B: 急加速<10次 且 急减速<6次
        C: 急加速<20次 且 急减速<12次
        D: 其他
    """
    if hard_accel_count < 5 and hard_brake_count < 3:
        return "A"
    elif hard_accel_count < 10 and hard_brake_count < 6:
        return "B"
    elif hard_accel_count < 20 and hard_brake_count < 12:
        return "C"
    else:
        return "D"


# ========== 急加速/急减速检测算法 ==========
# 急加速检测参数
ACCEL_SURGE_THRESHOLD = 50  # 功率突增阈值 (kW)，窗口内功率变化超过此值
ACCEL_PEAK_THRESHOLD = 40   # 峰值功率阈值 (kW)，当前功率需达到此值
ACCEL_WINDOW_SIZE = 5       # 检测窗口大小（数据点数）
ACCEL_COOLDOWN_SEC = 3      # 冷却时间（秒），避免同一次加速重复计数

# 急减速检测参数
#   1. 冬季电池温度低，动能回收受限，功率数据不准确
#   2. 机械制动时功率可能为正值或接近 0
#   3. 速度变化是减速的直接体现，更可靠
BRAKE_SPEED_DROP_MIN = 6      # 最小速度下降量 (km/h)，避免微小减速被误判
BRAKE_WINDOW_SIZE = 5         # 检测窗口大小（数据点数，约 3-4 秒）
BRAKE_COOLDOWN_SEC = 2.5      # 冷却时间（秒），避免同一次减速重复计数
BRAKE_MIN_SPEED = 10          # 最低速度限制 (km/h)，低于此速度不算急刹（停车抖动）


def _get_brake_threshold(speed_kmh: float) -> float:
    """根据速度获取减速率阈值

    高速时稍宽松（高速刹车本身就更剧烈），低速时更严格。
    这更符合人类对"急刹"的感知。

    Args:
        speed_kmh: 减速前的速度 (km/h)

    Returns:
        减速率阈值 (km/h/s)
    """
    if speed_kmh > 80:
        return 6.5  # 高速稍宽松
    elif speed_kmh > 50:
        return 7.0  # 中速标准
    else:
        return 7.5  # 低速更严格


def _count_hard_accel_events(power_data: list[tuple[float, datetime]]) -> int:
    """计算急加速事件数量

    算法：检测功率突增事件
    - 在 ACCEL_WINDOW_SIZE 个数据点的窗口内，如果功率从窗口最小值突增超过 ACCEL_SURGE_THRESHOLD
    - 且当前功率达到 ACCEL_PEAK_THRESHOLD
    - 则计为 1 次急加速事件
    - 事件之间需要间隔 ACCEL_COOLDOWN_SEC 秒才算新事件

    Args:
        power_data: [(power, timestamp), ...] 按时间排序的功率数据

    Returns:
        急加速事件数量
    """
    if len(power_data) < ACCEL_WINDOW_SIZE + 1:
        return 0

    events = 0
    last_event_time: datetime | None = None

    for i in range(ACCEL_WINDOW_SIZE, len(power_data)):
        power, timestamp = power_data[i]

        # 检查冷却期
        if last_event_time and (timestamp - last_event_time).total_seconds() < ACCEL_COOLDOWN_SEC:
            continue

        # 计算窗口内的最小功率
        window_min = min(d[0] for d in power_data[i - ACCEL_WINDOW_SIZE : i])

        # 计算功率突增量
        power_surge = power - window_min

        # 判断是否为急加速事件
        if power_surge >= ACCEL_SURGE_THRESHOLD and power >= ACCEL_PEAK_THRESHOLD:
            events += 1
            last_event_time = timestamp

    return events


def _count_hard_brake_events(speed_data: list[tuple[float, datetime]]) -> int:
    """计算急减速事件数量

    算法：检测速度骤降事件（基于速度变化率）
    - 在 BRAKE_WINDOW_SIZE 个数据点的窗口内，找到最高速度点
    - 计算从最高速度点到当前点的减速率 (km/h/s)
    - 根据速度动态获取阈值（高速稍宽松，低速更严格）
    - 如果减速率 >= 阈值 且速度下降 >= BRAKE_SPEED_DROP_MIN
    - 且当前速度 >= BRAKE_MIN_SPEED（排除停车抖动）
    - 则计为 1 次急减速事件
    - 事件之间需要间隔 BRAKE_COOLDOWN_SEC 秒才算新事件

    Args:
        speed_data: [(speed, timestamp), ...] 按时间排序的速度数据

    Returns:
        急减速事件数量
    """
    if len(speed_data) < BRAKE_WINDOW_SIZE + 1:
        return 0

    events = 0
    last_event_time: datetime | None = None

    for i in range(BRAKE_WINDOW_SIZE, len(speed_data)):
        current_speed, current_time = speed_data[i]

        # 跳过无效数据
        if current_speed is None:
            continue

        # 低于最低速度限制不算急刹（停车前抖动）
        if current_speed < BRAKE_MIN_SPEED:
            continue

        # 检查冷却期
        if last_event_time and (current_time - last_event_time).total_seconds() < BRAKE_COOLDOWN_SEC:
            continue

        # 获取窗口内的数据，找到最高速度点
        window_data = speed_data[i - BRAKE_WINDOW_SIZE : i]
        max_speed = 0.0
        max_speed_time: datetime | None = None

        for speed, timestamp in window_data:
            if speed is not None and speed > max_speed:
                max_speed = speed
                max_speed_time = timestamp

        if max_speed_time is None:
            continue

        # 计算速度下降量和时间间隔
        speed_drop = max_speed - current_speed
        time_span = (current_time - max_speed_time).total_seconds()

        if time_span <= 0:
            continue

        # 计算减速率 (km/h/s)
        decel_rate = speed_drop / time_span

        # 根据速度获取动态阈值
        threshold = _get_brake_threshold(max_speed)

        # 判断是否为急减速事件
        if decel_rate >= threshold and speed_drop >= BRAKE_SPEED_DROP_MIN:
            events += 1
            last_event_time = current_time

    return events


async def get_trip_driving_score(drive_id: int) -> DrivingScore | None:
    """获取单次行程的驾驶评分

    急加速检测：功率突增算法
        - 窗口内功率突增 >= 50kW 且峰值 >= 40kW
        - 冷却时间 3 秒

    急减速检测：速度变化率算法
        - 减速率 >= 7 km/h/s 且速度下降 >= 8 km/h
        - 冷却时间 3 秒
    """
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                # 获取该行程的所有位置数据（按时间排序）
                await cur.execute(
                    """
                    SELECT power, speed, date
                    FROM positions
                    WHERE drive_id = %s
                    ORDER BY date ASC
                    """,
                    (drive_id,),
                )
                rows = await cur.fetchall()

                if not rows:
                    return None

                # 转换为功率数据列表（用于急加速检测）
                power_data: list[tuple[float, datetime]] = [
                    (float(row[0]), row[2]) for row in rows if row[0] is not None
                ]

                # 转换为速度数据列表（用于急减速检测）
                speed_data: list[tuple[float, datetime]] = [
                    (float(row[1]), row[2]) for row in rows if row[1] is not None
                ]

                # 计算急加速和急减速事件数
                hard_accel_count = _count_hard_accel_events(power_data)
                hard_brake_count = _count_hard_brake_events(speed_data)
                grade = _calculate_grade(hard_accel_count, hard_brake_count)

                return DrivingScore(
                    hard_accel_count=hard_accel_count,
                    hard_brake_count=hard_brake_count,
                    grade=grade,
                )
    except Exception as e:
        logger.exception(f"查询行程驾驶评分失败: {e}")
        return None

