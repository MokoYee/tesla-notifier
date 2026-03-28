"""数据库查询模块"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg_pool import AsyncConnectionPool

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger
from tesla_notifier.traffic import TrafficSummary

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
    utc_dt = dt.replace(tzinfo=UTC)
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
    utc_dt = local_dt.astimezone(UTC)
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

    hard_accel_count: int  # 急加速次数
    hard_brake_count: int  # 急减速次数
    score: int  # 100 分制总分
    label: str  # 分档标签，如“优秀 / 稳健 / 正常 / 需注意 / 激进”
    road_context: str  # 路况标签，如“城市通勤 / 高速巡航 / 综合路况”
    hard_accel_rate: float  # 每 100 km 急加速次数
    hard_brake_rate: float  # 每 100 km 急减速次数
    confidence: float  # 样本置信度，主要用于短途平滑
    analysis_summary: str  # 自动分析摘要
    advice: str  # 自动建议
    traffic_label: str | None = None  # 行程交通画像
    traffic_summary: str | None = None  # 行程交通摘要
    traffic_sample_count: int = 0  # 路况采样次数
    traffic_stress_index: float | None = None  # 路况压力指数


@dataclass
class DrivingContext:
    """驾驶场景上下文"""

    urban_ratio: float  # 城市中低速场景占比
    highway_ratio: float  # 高速巡航场景占比
    overspeed_ratio: float  # 超高速占比（>120 km/h）
    stop_go_density: float  # 每 10 km 停走事件数
    road_context: str  # 路况标签


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
                return str(row[0])

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
                    return str(row[0])
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


async def _build_trip_data(
    conn: psycopg.AsyncConnection[Any],
    row: tuple[Any, ...],
) -> TripData:
    """将数据库行转换为 TripData。"""
    start_address = await resolve_location_name(conn, row[12], row[14], row[16])
    end_address = await resolve_location_name(conn, row[13], row[15], row[17])

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


async def get_recent_trips(car_id: str, limit: int = 5) -> list[TripData]:
    """获取最近结束的行程列表。"""
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
                        LIMIT %s
                        """,
                    (car_id, limit),
                )
                rows = await cur.fetchall()

            trips: list[TripData] = []
            for row in rows:
                trips.append(await _build_trip_data(conn, row))
            return trips
    except Exception as e:
        logger.exception(f"查询最近行程失败: {e}")
        return []


async def get_latest_trip(car_id: str) -> TripData | None:
    """获取最新行程。"""
    trips = await get_recent_trips(car_id, limit=1)
    return trips[0] if trips else None


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
                        COALESCE(
                            SUM(GREATEST(d.start_rated_range_km - d.end_rated_range_km, 0)),
                            0
                        ) as rated_range_used,
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
                efficiency = (
                    (rated_range_used * car_efficiency) / distance
                    if distance > 0
                    else 0
                )

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
                        COALESCE(
                            SUM(GREATEST(d.start_rated_range_km - d.end_rated_range_km, 0)),
                            0
                        ) as rated_range_used,
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
                efficiency = (
                    (rated_range_used * car_efficiency) / distance
                    if distance > 0
                    else 0
                )

                # 计算平均速度（km/h）
                avg_speed = (
                    distance / (total_duration_min / 60.0)
                    if total_duration_min > 0
                    else 0
                )

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
                        COALESCE(
                            SUM(GREATEST(d.start_rated_range_km - d.end_rated_range_km, 0)),
                            0
                        ) as rated_range_used,
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
                efficiency = (
                    (rated_range_used * car_efficiency) / distance
                    if distance > 0
                    else 0
                )

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


def _clamp(value: float, lower: float, upper: float) -> float:
    """限制数值范围"""
    return max(lower, min(value, upper))


def _rate_per_100km(event_count: int, distance_km: float) -> float:
    """将事件数归一化为每 100 km 频次"""
    if distance_km <= 0:
        return 0.0
    return (event_count / distance_km) * 100


def _calculate_score_label(score: int) -> str:
    """根据 100 分制总分输出中文标签"""
    if score >= 92:
        return "优秀"
    if score >= 85:
        return "稳健"
    if score >= 75:
        return "正常"
    if score >= 65:
        return "需注意"
    return "激进"


def _classify_road_context(
    urban_ratio: float,
    highway_ratio: float,
    stop_go_density: float,
) -> str:
    """根据速度结构和停走密度识别大致路况"""
    if stop_go_density >= 4.0 or urban_ratio >= 0.65:
        return "城市通勤"
    if highway_ratio >= 0.65:
        return "高速巡航"
    return "综合路况"


def _build_driving_context(
    speed_data: list[tuple[float, datetime]],
    distance_km: float,
) -> DrivingContext:
    """从速度序列中提取路况上下文

    这里不依赖外部地图 API，只基于 TeslaMate 已有速度轨迹估算。
    """
    if len(speed_data) < 2:
        return DrivingContext(
            urban_ratio=0.4,
            highway_ratio=0.2,
            overspeed_ratio=0.0,
            stop_go_density=0.0,
            road_context="综合路况",
        )

    moving_seconds = 0.0
    urban_seconds = 0.0
    highway_seconds = 0.0
    overspeed_seconds = 0.0
    stop_go_events = 0

    prev_speed, prev_time = speed_data[0]

    for current_speed, current_time in speed_data[1:]:
        interval_seconds = (current_time - prev_time).total_seconds()
        weighted_seconds = _clamp(interval_seconds, 0.0, 10.0)

        if prev_speed >= 10 and weighted_seconds > 0:
            moving_seconds += weighted_seconds

            if prev_speed < 60:
                urban_seconds += weighted_seconds
            if prev_speed >= 80:
                highway_seconds += weighted_seconds
            if prev_speed >= 120:
                overspeed_seconds += weighted_seconds

        # 识别典型停走场景：前一时刻仍在行驶，下一时刻已接近停车。
        if (
            interval_seconds > 0
            and interval_seconds <= 20
            and prev_speed >= 20
            and current_speed <= 5
        ):
            stop_go_events += 1

        prev_speed, prev_time = current_speed, current_time

    if moving_seconds <= 0:
        urban_ratio = 0.4
        highway_ratio = 0.2
        overspeed_ratio = 0.0
    else:
        urban_ratio = urban_seconds / moving_seconds
        highway_ratio = highway_seconds / moving_seconds
        overspeed_ratio = overspeed_seconds / moving_seconds

    stop_go_density = (stop_go_events / max(distance_km, 1.0)) * 10
    road_context = _classify_road_context(urban_ratio, highway_ratio, stop_go_density)

    return DrivingContext(
        urban_ratio=urban_ratio,
        highway_ratio=highway_ratio,
        overspeed_ratio=overspeed_ratio,
        stop_go_density=stop_go_density,
        road_context=road_context,
    )


def _get_expected_event_rates(context: DrivingContext) -> tuple[float, float]:
    """根据路况上下文估算可接受的动作基线

    核心目标不是“零急加速 / 零急刹”，而是允许城市通勤、高速巡航有不同基线。
    """
    expected_accel_rate = (
        3.0
        + 3.5 * context.urban_ratio
        + 1.5 * context.highway_ratio
        + 0.7 * context.stop_go_density
    )
    expected_brake_rate = (
        2.0
        + 5.5 * context.urban_ratio
        + 0.5 * context.highway_ratio
        + 1.2 * context.stop_go_density
    )

    return (
        _clamp(expected_accel_rate, 2.5, 12.0),
        _clamp(expected_brake_rate, 1.5, 14.0),
    )


def _apply_traffic_tolerance(
    expected_accel_rate: float,
    expected_brake_rate: float,
    traffic_summary: TrafficSummary | None,
) -> tuple[float, float]:
    """根据行程中的外部路况压力，适度放宽动作基线。"""
    if traffic_summary is None or traffic_summary.sample_count <= 0:
        return expected_accel_rate, expected_brake_rate

    pressure_factor = _clamp(traffic_summary.stress_index / 100.0, 0.0, 1.0)
    accel_buffer = pressure_factor * 1.6
    brake_buffer = pressure_factor * 3.8

    if traffic_summary.high_pressure_ratio >= 0.5:
        brake_buffer += 0.8

    return (
        _clamp(expected_accel_rate + accel_buffer, 2.5, 14.5),
        _clamp(expected_brake_rate + brake_buffer, 1.5, 18.0),
    )


def _calculate_excess_penalty(
    actual_rate: float,
    expected_rate: float,
    max_penalty: float,
) -> float:
    """按“基线内轻扣分、超基线重扣分”的方式计算事件扣分"""
    baseline_usage = _clamp(actual_rate / max(expected_rate, 1.0), 0.0, 1.0)
    base_penalty = baseline_usage * max_penalty * 0.18

    if actual_rate <= expected_rate:
        return base_penalty

    excess_ratio = (actual_rate - expected_rate) / max(expected_rate, 1.0)
    excess_penalty = _clamp(
        excess_ratio * max_penalty * 0.72,
        0.0,
        max_penalty - base_penalty,
    )
    return base_penalty + excess_penalty


def _calculate_speed_discipline_penalty(
    speed_data: list[tuple[float, datetime]],
    context: DrivingContext,
) -> float:
    """基于超高速占比与峰值速度进行附加扣分"""
    if not speed_data:
        return 0.0

    max_speed = max(speed for speed, _ in speed_data)
    overspeed_penalty = _clamp(context.overspeed_ratio * 18.0, 0.0, 10.0)
    peak_penalty = 0.0

    if max_speed > 130:
        peak_penalty = _clamp(((max_speed - 130) / 10.0) * 2.5, 0.0, 6.0)

    return min(14.0, overspeed_penalty + peak_penalty)


def _build_driving_analysis(
    context: DrivingContext,
    hard_accel_rate: float,
    hard_brake_rate: float,
    expected_accel_rate: float,
    expected_brake_rate: float,
    confidence: float,
    traffic_summary: TrafficSummary | None,
) -> tuple[str, str]:
    """生成自动分析摘要与建议

    目标是把评分结果解释成“为什么是这个分数”，而不是只给一个数字。
    """
    context_prefix = {
        "城市通勤": "本次以城市通勤为主",
        "高速巡航": "本次以高速巡航为主",
        "综合路况": "本次路况较为综合",
    }.get(context.road_context, "本次路况较为综合")
    traffic_clause = _build_traffic_clause(traffic_summary)

    accel_ratio = hard_accel_rate / max(expected_accel_rate, 1.0)
    brake_ratio = hard_brake_rate / max(expected_brake_rate, 1.0)

    positives: list[str] = []
    cautions: list[str] = []

    if accel_ratio <= 0.6:
        positives.append("提速动作较克制")
    elif accel_ratio >= 1.35:
        cautions.append("提速偏急")

    if brake_ratio <= 0.7:
        positives.append("制动预判较稳")
    elif brake_ratio >= 1.2:
        cautions.append("制动偏多")

    if context.overspeed_ratio >= 0.12:
        cautions.append("高速阶段车速偏快")

    if confidence < 0.35:
        summary_parts = [context_prefix]
        if traffic_clause:
            summary_parts.append(traffic_clause)
        summary = "，".join(summary_parts) + "，但行程较短，当前结果更适合作为趋势参考。"
        return summary, "建议结合后续多次行程一起观察，避免对超短途过度解读。"

    summary_parts = [context_prefix]
    if traffic_clause:
        summary_parts.append(traffic_clause)

    if cautions:
        primary_issue = cautions[0]
        summary = "，".join(summary_parts + [primary_issue]) + "。"
    elif positives:
        summary = "，".join(summary_parts + [positives[0]]) + "，整体节奏较平顺。"
    else:
        summary = "，".join(summary_parts) + "，整体驾驶表现基本稳定。"

    if "制动偏多" in cautions:
        if traffic_summary and traffic_summary.high_pressure_ratio >= 0.5:
            advice = "本次拥堵路段较多，建议进一步拉开跟车距离，减少跟停带来的急刹。"
        else:
            advice = "建议提前观察前车与路口变化，尽量更早松电并预留车距。"
    elif "提速偏急" in cautions:
        if traffic_summary and traffic_summary.high_pressure_ratio >= 0.5:
            advice = "拥堵路段频繁补电门收益有限，建议减少二次提速，节奏会更稳。"
        else:
            advice = "建议减少连续深踩电门，拉开提速节奏会更稳。"
    elif "高速阶段车速偏快" in cautions:
        advice = "建议高速阶段更早收电控制车速，保持更稳定的巡航区间。"
    elif (
        traffic_summary
        and traffic_summary.traffic_label in {"高压拥堵", "明显拥堵"}
    ):
        advice = "本次外部路况压力较高，评分已按拥堵情况做缓冲，继续保持提前预判即可。"
    elif context.road_context == "城市通勤":
        advice = "城市路况波动较大，继续保持当前预判和跟车节奏即可。"
    elif context.road_context == "高速巡航":
        advice = "高速路况下保持均匀提速和稳定巡航，有助于长期维持高分。"
    else:
        advice = "继续保持平顺提速和提前预判，分数会更稳定。"

    return summary, advice


def _build_traffic_clause(traffic_summary: TrafficSummary | None) -> str | None:
    """把交通画像转成适合拼接到分析摘要中的短句。"""
    if traffic_summary is None or traffic_summary.sample_count <= 0:
        return None

    return {
        "高压拥堵": "沿途拥堵压力较高",
        "明显拥堵": "沿途缓行较多",
        "轻度拥堵": "沿途有轻度拥堵",
        "整体畅通": "沿途整体较为畅通",
    }.get(traffic_summary.traffic_label, None)


# ========== 急加速/急减速检测算法 ==========
# 急加速检测参数
ACCEL_SURGE_THRESHOLD = 50  # 功率突增阈值 (kW)，窗口内功率变化超过此值
ACCEL_PEAK_THRESHOLD = 40   # 峰值功率阈值 (kW)，当前功率需达到此值
ACCEL_SPEED_GAIN_MIN = 8    # 最小速度增量 (km/h)，避免把普通补电门误判为急加速
ACCEL_MIN_SPEED = 15        # 最低速度限制 (km/h)，排除挪车和低速蠕行
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


def _count_hard_accel_events(motion_data: list[tuple[float, float, datetime]]) -> int:
    """计算急加速事件数量

    算法：检测“功率突增 + 速度明显提升”的复合事件
    - 在 ACCEL_WINDOW_SIZE 个数据点的窗口内，如果功率从窗口最小值突增超过 ACCEL_SURGE_THRESHOLD
    - 且当前功率达到 ACCEL_PEAK_THRESHOLD
    - 且窗口内速度提升达到 ACCEL_SPEED_GAIN_MIN
    - 且当前速度 >= ACCEL_MIN_SPEED
    - 则计为 1 次急加速事件
    - 事件之间需要间隔 ACCEL_COOLDOWN_SEC 秒才算新事件

    Args:
        motion_data: [(power, speed, timestamp), ...] 按时间排序的轨迹数据

    Returns:
        急加速事件数量
    """
    if len(motion_data) < ACCEL_WINDOW_SIZE + 1:
        return 0

    events = 0
    last_event_time: datetime | None = None

    for i in range(ACCEL_WINDOW_SIZE, len(motion_data)):
        power, speed, timestamp = motion_data[i]

        if speed < ACCEL_MIN_SPEED:
            continue

        # 检查冷却期
        if (
            last_event_time
            and (timestamp - last_event_time).total_seconds() < ACCEL_COOLDOWN_SEC
        ):
            continue

        window_slice = motion_data[i - ACCEL_WINDOW_SIZE : i]
        window_min_power = min(d[0] for d in window_slice)
        window_min_speed = min(d[1] for d in window_slice)

        # 计算功率突增量
        power_surge = power - window_min_power
        speed_gain = speed - window_min_speed

        # 判断是否为急加速事件
        if (
            power_surge >= ACCEL_SURGE_THRESHOLD
            and power >= ACCEL_PEAK_THRESHOLD
            and speed_gain >= ACCEL_SPEED_GAIN_MIN
        ):
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
        if (
            last_event_time
            and (current_time - last_event_time).total_seconds() < BRAKE_COOLDOWN_SEC
        ):
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


async def get_trip_driving_score(
    drive_id: int,
    traffic_summary: TrafficSummary | None = None,
) -> DrivingScore | None:
    """获取单次行程的驾驶评分

    评分采用 100 分制，并引入按里程归一化和路况修正，避免：
    1. 长途因为“绝对事件数更多”而天然吃亏
    2. 短途因为样本太少而轻易拿到高分
    """
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT distance
                    FROM drives
                    WHERE id = %s
                    """,
                    (drive_id,),
                )
                drive_row = await cur.fetchone()
                distance_km = float(drive_row[0]) if drive_row and drive_row[0] else 0.0

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

                # 转换为轨迹数据列表（用于急加速检测）
                motion_data: list[tuple[float, float, datetime]] = [
                    (float(row[0]), float(row[1]), row[2])
                    for row in rows
                    if row[0] is not None and row[1] is not None
                ]

                # 转换为速度数据列表（用于急减速检测）
                speed_data: list[tuple[float, datetime]] = [
                    (float(row[1]), row[2]) for row in rows if row[1] is not None
                ]

                # 计算急加速和急减速事件数
                hard_accel_count = _count_hard_accel_events(motion_data)
                hard_brake_count = _count_hard_brake_events(speed_data)
                hard_accel_rate = _rate_per_100km(hard_accel_count, distance_km)
                hard_brake_rate = _rate_per_100km(hard_brake_count, distance_km)

                context = _build_driving_context(speed_data, distance_km)
                expected_accel_rate, expected_brake_rate = _get_expected_event_rates(
                    context
                )
                expected_accel_rate, expected_brake_rate = _apply_traffic_tolerance(
                    expected_accel_rate,
                    expected_brake_rate,
                    traffic_summary,
                )

                smooth_penalty = _calculate_excess_penalty(
                    hard_accel_rate,
                    expected_accel_rate,
                    max_penalty=18.0,
                ) + _calculate_excess_penalty(
                    hard_brake_rate,
                    expected_brake_rate,
                    max_penalty=22.0,
                )
                speed_penalty = _calculate_speed_discipline_penalty(speed_data, context)
                raw_score = 100.0 - smooth_penalty - speed_penalty

                # 短途样本天然偏少，向中性分数回归，避免一两次停车就把分数拉爆。
                confidence = _clamp(distance_km / 15.0, 0.0, 1.0)
                blended_score = raw_score * confidence + 85.0 * (1.0 - confidence)
                final_score = round(_clamp(blended_score, 0.0, 100.0))
                label = _calculate_score_label(final_score)
                analysis_summary, advice = _build_driving_analysis(
                    context,
                    hard_accel_rate,
                    hard_brake_rate,
                    expected_accel_rate,
                    expected_brake_rate,
                    confidence,
                    traffic_summary,
                )

                return DrivingScore(
                    hard_accel_count=hard_accel_count,
                    hard_brake_count=hard_brake_count,
                    score=final_score,
                    label=label,
                    road_context=context.road_context,
                    hard_accel_rate=hard_accel_rate,
                    hard_brake_rate=hard_brake_rate,
                    confidence=confidence,
                    analysis_summary=analysis_summary,
                    advice=advice,
                    traffic_label=(
                        traffic_summary.traffic_label
                        if traffic_summary is not None
                        else None
                    ),
                    traffic_summary=(
                        traffic_summary.summary
                        if traffic_summary is not None
                        else None
                    ),
                    traffic_sample_count=(
                        traffic_summary.sample_count
                        if traffic_summary is not None
                        else 0
                    ),
                    traffic_stress_index=(
                        traffic_summary.stress_index
                        if traffic_summary is not None
                        else None
                    ),
                )
    except Exception as e:
        logger.exception(f"查询行程驾驶评分失败: {e}")
        return None
