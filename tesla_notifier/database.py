"""数据库查询模块"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("database")


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

    hard_accel_pct: float  # 急加速百分比
    hard_brake_pct: float  # 急减速百分比
    grade: str  # 评分等级 A/B/C/D


async def get_connection() -> psycopg.AsyncConnection[Any]:
    """获取数据库连接"""
    return await psycopg.AsyncConnection.connect(config.db_dsn)


async def get_latest_trip(car_id: str) -> TripData | None:
    """获取最新行程"""
    try:
        async with await get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        d.id,
                        d.car_id,
                        d.start_date,
                        d.end_date,
                        COALESCE(sa.name, '未知地点') as start_address,
                        COALESCE(ea.name, '未知地点') as end_address,
                        d.distance,
                        d.duration_min,
                        d.start_rated_range_km,
                        d.end_rated_range_km,
                        d.start_battery_level,
                        d.end_battery_level,
                        d.outside_temp_avg,
                        d.speed_max
                    FROM drives d
                    LEFT JOIN addresses sa ON d.start_address_id = sa.id
                    LEFT JOIN addresses ea ON d.end_address_id = ea.id
                    WHERE d.car_id = %s AND d.end_date IS NOT NULL
                    ORDER BY d.end_date DESC
                    LIMIT 1
                    """,
                    (car_id,),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                return TripData(
                    id=row[0],
                    car_id=row[1],
                    start_date=row[2].isoformat() if row[2] else "",
                    end_date=row[3].isoformat() if row[3] else "",
                    start_address=row[4],
                    end_address=row[5],
                    distance=float(row[6] or 0),
                    duration_min=float(row[7] or 0),
                    start_rated_range_km=float(row[8] or 0),
                    end_rated_range_km=float(row[9] or 0),
                    start_battery_level=int(row[10] or 0),
                    end_battery_level=int(row[11] or 0),
                    outside_temp_avg=float(row[12]) if row[12] else None,
                    speed_max=float(row[13]) if row[13] else None,
                )
    except Exception as e:
        logger.exception(f"查询最新行程失败: {e}")
        return None


async def get_latest_charging(car_id: str) -> ChargingData | None:
    """获取最新充电记录"""
    try:
        async with await get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        c.id,
                        c.car_id,
                        c.start_date,
                        c.end_date,
                        COALESCE(a.name, g.name, '未知地点') as location,
                        c.duration_min,
                        c.start_battery_level,
                        c.end_battery_level,
                        c.charge_energy_added,
                        c.charger_power_max,
                        c.start_rated_range_km,
                        c.end_rated_range_km,
                        c.outside_temp_avg
                    FROM charging_processes c
                    LEFT JOIN addresses a ON c.address_id = a.id
                    LEFT JOIN geofences g ON c.geofence_id = g.id
                    WHERE c.car_id = %s AND c.end_date IS NOT NULL
                    ORDER BY c.end_date DESC
                    LIMIT 1
                    """,
                    (car_id,),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                return ChargingData(
                    id=row[0],
                    car_id=row[1],
                    start_date=row[2].isoformat() if row[2] else "",
                    end_date=row[3].isoformat() if row[3] else "",
                    location=row[4],
                    duration_min=float(row[5] or 0),
                    start_battery_level=int(row[6] or 0),
                    end_battery_level=int(row[7] or 0),
                    charge_energy_added=float(row[8] or 0),
                    charger_power_max=float(row[9] or 0),
                    start_rated_range_km=float(row[10] or 0),
                    end_rated_range_km=float(row[11] or 0),
                    outside_temp_avg=float(row[12]) if row[12] else None,
                )
    except Exception as e:
        logger.exception(f"查询最新充电记录失败: {e}")
        return None


async def get_yesterday_summary(car_id: str) -> DrivingSummary | None:
    """获取昨日驾驶汇总"""
    try:
        async with await get_connection() as conn:
            async with conn.cursor() as cur:
                tz = ZoneInfo(config.timezone)
                yesterday = datetime.now(tz) - timedelta(days=1)
                date_str = yesterday.strftime("%Y-%m-%d")

                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as trips,
                        COALESCE(SUM(distance), 0) as total_distance,
                        COALESCE(SUM(start_rated_range_km - end_rated_range_km), 0) as energy_used
                    FROM drives
                    WHERE car_id = %s
                        AND DATE(start_date) = %s
                        AND end_date IS NOT NULL
                    """,
                    (car_id, date_str),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                trips = int(row[0] or 0)
                distance = float(row[1] or 0)
                energy_used = float(row[2] or 0)

                if trips == 0:
                    return None

                # 计算效率 (Wh/km)，假设 1km 续航 ≈ 150Wh
                efficiency = (energy_used * 150) / distance if distance > 0 else 0

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
        async with await get_connection() as conn:
            async with conn.cursor() as cur:
                tz = ZoneInfo(config.timezone)
                end_date = datetime.now(tz).replace(tzinfo=None)
                start_date = end_date - timedelta(days=7)

                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as trips,
                        COALESCE(SUM(distance), 0) as total_distance,
                        COALESCE(SUM(start_rated_range_km - end_rated_range_km), 0) as energy_used
                    FROM drives
                    WHERE car_id = %s
                        AND start_date >= %s
                        AND start_date < %s
                        AND end_date IS NOT NULL
                    """,
                    (car_id, start_date, end_date),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                trips = int(row[0] or 0)
                distance = float(row[1] or 0)
                energy_used = float(row[2] or 0)

                if trips == 0:
                    return None

                efficiency = energy_used / distance if distance > 0 else 0

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
        async with await get_connection() as conn:
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
                        COALESCE(SUM(distance), 0) as total_distance,
                        COALESCE(SUM(start_rated_range_km - end_rated_range_km), 0) as energy_used,
                        COALESCE(MAX(distance), 0) as longest_trip
                    FROM drives
                    WHERE car_id = %s
                        AND start_date >= %s
                        AND start_date < %s
                        AND end_date IS NOT NULL
                    """,
                    (car_id, start_date, end_date),
                )
                row = await cur.fetchone()

                if not row:
                    return None

                trips = int(row[0] or 0)
                distance = float(row[1] or 0)
                energy_used = float(row[2] or 0)
                longest_trip = float(row[3] or 0)

                if trips == 0:
                    return None

                efficiency = energy_used / distance if distance > 0 else 0

                return DrivingSummary(
                    total_trips=trips,
                    total_distance=distance,
                    avg_efficiency=efficiency,
                    longest_trip=longest_trip,
                )
    except Exception as e:
        logger.exception(f"查询月汇总失败: {e}")
        return None

async def get_vehicle_last_position(car_id: str) -> tuple[float, float] | None:
    """获取车辆最后位置"""
    try:
        async with await get_connection() as conn:
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


def _calculate_grade(hard_accel_pct: float, hard_brake_pct: float) -> str:
    """根据急加速和急减速百分比计算评分等级"""
    if hard_accel_pct < 3 and hard_brake_pct < 5:
        return "A"
    elif hard_accel_pct < 5 and hard_brake_pct < 8:
        return "B"
    elif hard_accel_pct < 8 and hard_brake_pct < 12:
        return "C"
    else:
        return "D"


async def get_trip_driving_score(drive_id: int) -> DrivingScore | None:
    """获取单次行程的驾驶评分"""
    try:
        async with await get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as total_points,
                        COUNT(*) FILTER (WHERE power >= 50) as hard_accel_count,
                        COUNT(*) FILTER (WHERE power <= -40) as hard_brake_count
                    FROM positions
                    WHERE drive_id = %s AND power IS NOT NULL
                    """,
                    (drive_id,),
                )
                row = await cur.fetchone()

                if not row or row[0] == 0:
                    return None

                total = row[0]
                hard_accel_pct = (row[1] / total) * 100
                hard_brake_pct = (row[2] / total) * 100
                grade = _calculate_grade(hard_accel_pct, hard_brake_pct)

                return DrivingScore(
                    hard_accel_pct=hard_accel_pct,
                    hard_brake_pct=hard_brake_pct,
                    grade=grade,
                )
    except Exception as e:
        logger.exception(f"查询行程驾驶评分失败: {e}")
        return None


async def get_daily_driving_score(car_id: str, date_str: str) -> DrivingScore | None:
    """获取某天的驾驶评分"""
    try:
        async with await get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        COUNT(*) as total_points,
                        COUNT(*) FILTER (WHERE p.power >= 50) as hard_accel_count,
                        COUNT(*) FILTER (WHERE p.power <= -40) as hard_brake_count
                    FROM positions p
                    JOIN drives d ON p.drive_id = d.id
                    WHERE d.car_id = %s
                        AND DATE(d.start_date) = %s
                        AND p.power IS NOT NULL
                    """,
                    (car_id, date_str),
                )
                row = await cur.fetchone()

                if not row or row[0] == 0:
                    return None

                total = row[0]
                hard_accel_pct = (row[1] / total) * 100
                hard_brake_pct = (row[2] / total) * 100
                grade = _calculate_grade(hard_accel_pct, hard_brake_pct)

                return DrivingScore(
                    hard_accel_pct=hard_accel_pct,
                    hard_brake_pct=hard_brake_pct,
                    grade=grade,
                )
    except Exception as e:
        logger.exception(f"查询每日驾驶评分失败: {e}")
        return None
