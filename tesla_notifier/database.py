"""数据库查询模块"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

import psycopg
from psycopg_pool import AsyncConnectionPool

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("database")

# 全局连接池（延迟初始化）
_pool: AsyncConnectionPool | None = None


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
    3. 地址名称 address_name

    Args:
        conn: 数据库连接
        geofence_id: 收藏地点 ID（可能为 None）
        position_id: 位置点 ID（用于获取经纬度）
        address_name: 地址名称（兜底）

    Returns:
        解析后的位置名称
    """
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
        if position_id:
            await cur.execute(
                """
                SELECT g.name
                FROM geofences g, positions p
                WHERE p.id = %s
                  AND earth_box(ll_to_earth(g.latitude, g.longitude), g.radius)
                      @> ll_to_earth(p.latitude, p.longitude)
                  AND earth_distance(ll_to_earth(g.latitude, g.longitude),
                                     ll_to_earth(p.latitude, p.longitude)) < g.radius
                ORDER BY earth_distance(ll_to_earth(g.latitude, g.longitude),
                                        ll_to_earth(p.latitude, p.longitude)) ASC
                LIMIT 1
                """,
                (position_id,),
            )
            row = await cur.fetchone()
            if row and row[0]:
                return row[0]

        # 3. 使用地址名称
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
                    start_date=row[2].isoformat() if row[2] else "",
                    end_date=row[3].isoformat() if row[3] else "",
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
                        c.charger_power_max,
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

                # 解析充电位置名称
                location = await resolve_location_name(
                    conn, row[12], row[13], row[14]
                )

                return ChargingData(
                    id=row[0],
                    car_id=row[1],
                    start_date=row[2].isoformat() if row[2] else "",
                    end_date=row[3].isoformat() if row[3] else "",
                    location=location,
                    duration_min=float(row[4] or 0),
                    start_battery_level=int(row[5] or 0),
                    end_battery_level=int(row[6] or 0),
                    charge_energy_added=float(row[7] or 0),
                    charger_power_max=float(row[8] or 0),
                    start_rated_range_km=float(row[9] or 0),
                    end_rated_range_km=float(row[10] or 0),
                    outside_temp_avg=float(row[11]) if row[11] else None,
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

                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as trips,
                        COALESCE(SUM(d.distance), 0) as total_distance,
                        COALESCE(SUM(GREATEST(d.start_rated_range_km - d.end_rated_range_km, 0)), 0) as rated_range_used,
                        c.efficiency as car_efficiency
                    FROM drives d
                    JOIN cars c ON d.car_id = c.id
                    WHERE d.car_id = %s
                        AND DATE(d.start_date) = %s
                        AND d.end_date IS NOT NULL
                    GROUP BY c.efficiency
                    """,
                    (car_id, date_str),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                trips = int(row[0] or 0)
                distance = float(row[1] or 0)
                rated_range_used = float(row[2] or 0)
                car_efficiency = float(row[3] or 150.0)  # 默认 150 Wh/km

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
    """获取周驾驶汇总"""
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                tz = ZoneInfo(config.timezone)
                end_date = datetime.now(tz).replace(tzinfo=None)
                start_date = end_date - timedelta(days=7)

                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as trips,
                        COALESCE(SUM(d.distance), 0) as total_distance,
                        COALESCE(SUM(GREATEST(d.start_rated_range_km - d.end_rated_range_km, 0)), 0) as rated_range_used,
                        c.efficiency as car_efficiency
                    FROM drives d
                    JOIN cars c ON d.car_id = c.id
                    WHERE d.car_id = %s
                        AND d.start_date >= %s
                        AND d.start_date < %s
                        AND d.end_date IS NOT NULL
                    GROUP BY c.efficiency
                    """,
                    (car_id, start_date, end_date),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                trips = int(row[0] or 0)
                distance = float(row[1] or 0)
                rated_range_used = float(row[2] or 0)
                car_efficiency = float(row[3] or 150.0)  # 默认 150 Wh/km

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
        logger.exception(f"查询周汇总失败: {e}")
        return None


async def get_monthly_summary(car_id: str) -> DrivingSummary | None:
    """获取月驾驶汇总"""
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                tz = ZoneInfo(config.timezone)
                now = datetime.now(tz)
                end_date = datetime(now.year, now.month, 1)
                if now.month == 1:
                    start_date = datetime(now.year - 1, 12, 1)
                else:
                    start_date = datetime(now.year, now.month - 1, 1)

                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as trips,
                        COALESCE(SUM(d.distance), 0) as total_distance,
                        COALESCE(SUM(GREATEST(d.start_rated_range_km - d.end_rated_range_km, 0)), 0) as rated_range_used,
                        COALESCE(MAX(d.distance), 0) as longest_trip,
                        c.efficiency as car_efficiency
                    FROM drives d
                    JOIN cars c ON d.car_id = c.id
                    WHERE d.car_id = %s
                        AND d.start_date >= %s
                        AND d.start_date < %s
                        AND d.end_date IS NOT NULL
                    GROUP BY c.efficiency
                    """,
                    (car_id, start_date, end_date),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                trips = int(row[0] or 0)
                distance = float(row[1] or 0)
                rated_range_used = float(row[2] or 0)
                longest_trip = float(row[3] or 0)
                car_efficiency = float(row[4] or 150.0)  # 默认 150 Wh/km

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

    TeslaMate 根据充电数据动态校准此值，比固定 150 更准确。
    如果查询失败或值为空，返回默认值 150.0。
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
                    return float(row[0])
                return 150.0  # 默认值
    except Exception as e:
        logger.exception(f"查询车辆 efficiency 失败: {e}")
        return 150.0


async def get_vehicle_last_position(car_id: str) -> tuple[float, float] | None:
    """获取车辆最后位置"""
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT latitude, longitude
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

                return (float(row[0]), float(row[1]))
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


async def get_trip_driving_score(drive_id: int) -> DrivingScore | None:
    """获取单次行程的驾驶评分

    阈值定义:
        急加速: power >= 100kW
        急减速: power <= -55kW
    """
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE power >= 100) as hard_accel_count,
                        COUNT(*) FILTER (WHERE power <= -55) as hard_brake_count
                    FROM positions
                    WHERE drive_id = %s AND power IS NOT NULL
                    """,
                    (drive_id,),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                hard_accel_count = int(row[0] or 0)
                hard_brake_count = int(row[1] or 0)
                grade = _calculate_grade(hard_accel_count, hard_brake_count)

                return DrivingScore(
                    hard_accel_count=hard_accel_count,
                    hard_brake_count=hard_brake_count,
                    grade=grade,
                )
    except Exception as e:
        logger.exception(f"查询行程驾驶评分失败: {e}")
        return None


async def get_daily_driving_score(car_id: str, date_str: str) -> DrivingScore | None:
    """获取某天的驾驶评分

    阈值定义:
        急加速: power >= 100kW
        急减速: power <= -55kW
    """
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE p.power >= 100) as hard_accel_count,
                        COUNT(*) FILTER (WHERE p.power <= -55) as hard_brake_count
                    FROM positions p
                    JOIN drives d ON p.drive_id = d.id
                    WHERE d.car_id = %s
                        AND DATE(d.start_date) = %s
                        AND p.power IS NOT NULL
                    """,
                    (car_id, date_str),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                hard_accel_count = int(row[0] or 0)
                hard_brake_count = int(row[1] or 0)
                grade = _calculate_grade(hard_accel_count, hard_brake_count)

                return DrivingScore(
                    hard_accel_count=hard_accel_count,
                    hard_brake_count=hard_brake_count,
                    grade=grade,
                )
    except Exception as e:
        logger.exception(f"查询每日驾驶评分失败: {e}")
        return None
