"""高德地图 API 模块

用于逆地理编码（坐标转地址），提供更友好的中文地址显示。

地址解析优先级：
1. AOI（兴趣面）：当坐标在区域内部（distance=0）时，直接使用区域名称
2. POI（兴趣点）：100米内的 POI，优先使用 name 字段
3. formatted_address：高德返回的格式化地址
"""

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("amap")

# 高德逆地理编码 API
AMAP_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"

# 请求超时（秒）
REQUEST_TIMEOUT = 10.0
AMAP_MIN_REQUEST_INTERVAL_SECONDS = 1.0
AMAP_REGEOCODE_SUCCESS_TTL_SECONDS = 24 * 60 * 60
AMAP_REGEOCODE_FAILURE_TTL_SECONDS = 10 * 60
AMAP_REGEOCODE_LIMIT_TTL_SECONDS = 15 * 60

_AMAP_REQUEST_SEMAPHORE = asyncio.Semaphore(1)
_REGEOCODE_LOCK = asyncio.Lock()
_last_amap_request_at = 0.0
_regeocode_cache: dict[str, "_RegeocodeCacheEntry"] = {}
_regeocode_fetch_locks: dict[str, asyncio.Lock] = {}

# WGS-84 转 GCJ-02 坐标转换常量
_A = 6378245.0  # 长半轴
_EE = 0.00669342162296594323  # 偏心率平方


@dataclass(frozen=True)
class AmapRegeoResult:
    """高德逆地理编码的结构化结果。"""

    address: str | None
    adcode: str | None


@dataclass(frozen=True)
class _RegeocodeCacheEntry:
    """逆地理编码缓存项，允许缓存失败结果。"""

    result: AmapRegeoResult | None
    expires_at: float


async def amap_request_json(
    url: str,
    params: dict[str, str],
    service_name: str,
    timeout: float = REQUEST_TIMEOUT,
) -> dict[str, Any] | None:
    """串行调用高德 WebService，避免多个功能共享同一 Key 时互相打爆。"""
    async with _AMAP_REQUEST_SEMAPHORE:
        await _wait_for_amap_request_slot()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params)

            if response.status_code != 200:
                logger.warning(f"{service_name}请求失败: HTTP {response.status_code}")
                return None

            data = response.json()
            if not isinstance(data, dict):
                logger.warning(f"{service_name}返回格式异常")
                return None

            return data
        except httpx.TimeoutException:
            logger.warning(f"{service_name}请求超时")
            return None
        except Exception as exc:
            logger.exception(f"{service_name}调用异常: {exc}")
            return None


async def _wait_for_amap_request_slot() -> None:
    """保证高德请求之间至少间隔固定秒数。"""
    global _last_amap_request_at

    now = time.monotonic()
    elapsed = now - _last_amap_request_at
    if elapsed < AMAP_MIN_REQUEST_INTERVAL_SECONDS:
        await asyncio.sleep(AMAP_MIN_REQUEST_INTERVAL_SECONDS - elapsed)

    _last_amap_request_at = time.monotonic()


def _wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    """WGS-84 坐标转 GCJ-02 坐标（火星坐标系）

    Args:
        lat: WGS-84 纬度
        lon: WGS-84 经度

    Returns:
        (GCJ-02 纬度, GCJ-02 经度)
    """
    # 判断是否在中国境外
    if _out_of_china(lat, lon):
        return lat, lon

    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    mglat = lat + dlat
    mglon = lon + dlon
    return mglat, mglon


def wgs84_to_gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    """对外暴露的坐标转换方法。"""
    return _wgs84_to_gcj02(latitude, longitude)


def _out_of_china(lat: float, lon: float) -> bool:
    """判断是否在中国境外"""
    return not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(x: float, y: float) -> float:
    """纬度转换"""
    ret = (
        -100.0
        + 2.0 * x
        + 3.0 * y
        + 0.2 * y * y
        + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
    )
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    """经度转换"""
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


async def reverse_geocode(latitude: float, longitude: float) -> str | None:
    """逆地理编码：坐标转地址

    优先级：
    1. AOI（兴趣面）：当 distance=0 时，表示坐标在区域内部，直接使用区域名称
    2. POI（兴趣点）：100米内的 POI，优先使用 name 字段
    3. formatted_address：高德返回的格式化地址

    Args:
        latitude: WGS-84 纬度
        longitude: WGS-84 经度

    Returns:
        地址字符串，失败返回 None
    """
    result = await reverse_geocode_detail(latitude, longitude)
    return result.address if result else None


async def get_adcode(latitude: float, longitude: float) -> str | None:
    """从逆地理编码结果中提取行政区编码。"""
    result = await reverse_geocode_detail(latitude, longitude)
    return result.adcode if result else None


async def reverse_geocode_detail(
    latitude: float,
    longitude: float,
) -> AmapRegeoResult | None:
    """逆地理编码并返回地址和 adcode，内部带缓存与失败负缓存。"""
    if not config.amap_key:
        return None

    cache_key = _build_regeocode_cache_key(latitude, longitude)
    cache_hit, cached = await _get_cached_regeocode(cache_key)
    if cache_hit:
        return cached

    fetch_lock = await _get_regeocode_fetch_lock(cache_key)
    async with fetch_lock:
        try:
            cache_hit, cached = await _get_cached_regeocode(cache_key)
            if cache_hit:
                return cached

            result, ttl_seconds = await _fetch_reverse_geocode_detail(latitude, longitude)

            async with _REGEOCODE_LOCK:
                _regeocode_cache[cache_key] = _RegeocodeCacheEntry(
                    result=result,
                    expires_at=time.monotonic() + ttl_seconds,
                )

            return result
        finally:
            async with _REGEOCODE_LOCK:
                if _regeocode_fetch_locks.get(cache_key) is fetch_lock:
                    _regeocode_fetch_locks.pop(cache_key, None)


async def _get_cached_regeocode(
    cache_key: str,
) -> tuple[bool, AmapRegeoResult | None]:
    """读取未过期的逆地理编码缓存。"""
    now = time.monotonic()
    async with _REGEOCODE_LOCK:
        cached = _regeocode_cache.get(cache_key)
        if cached is not None and cached.expires_at > now:
            return True, cached.result
        if cached is not None:
            _regeocode_cache.pop(cache_key, None)
    return False, None


async def _get_regeocode_fetch_lock(cache_key: str) -> asyncio.Lock:
    """获取单个坐标网格的请求锁，避免缓存击穿。"""
    async with _REGEOCODE_LOCK:
        fetch_lock = _regeocode_fetch_locks.get(cache_key)
        if fetch_lock is None:
            fetch_lock = asyncio.Lock()
            _regeocode_fetch_locks[cache_key] = fetch_lock
        return fetch_lock


async def _fetch_reverse_geocode_detail(
    latitude: float,
    longitude: float,
) -> tuple[AmapRegeoResult | None, int]:
    """实际请求高德逆地理编码，返回结果和应缓存的 TTL。"""
    # WGS-84 转 GCJ-02（高德地图使用火星坐标系）
    gcj_lat, gcj_lon = wgs84_to_gcj02(latitude, longitude)

    params = {
        "key": config.amap_key,
        "location": f"{gcj_lon:.6f},{gcj_lat:.6f}",  # 高德格式：经度,纬度
        "extensions": "all",
        "output": "json",
        "radius": "200",  # 搜索半径 200 米
    }
    data = await amap_request_json(AMAP_REGEO_URL, params, "高德逆地理编码")
    if data is None:
        return None, AMAP_REGEOCODE_FAILURE_TTL_SECONDS

    if data.get("status") != "1":
        info = str(data.get("info", "未知错误"))
        infocode = str(data.get("infocode", "N/A"))
        logger.warning(f"高德逆地理编码返回错误: {info} ({infocode})")
        ttl_seconds = (
            AMAP_REGEOCODE_LIMIT_TTL_SECONDS
            if _is_amap_rate_limit_error(info, infocode)
            else AMAP_REGEOCODE_FAILURE_TTL_SECONDS
        )
        return None, ttl_seconds

    result = _parse_regeocode_result(data)
    ttl_seconds = (
        AMAP_REGEOCODE_SUCCESS_TTL_SECONDS
        if result is not None
        else AMAP_REGEOCODE_FAILURE_TTL_SECONDS
    )
    return result, ttl_seconds


def _parse_regeocode_result(data: dict[str, Any]) -> AmapRegeoResult | None:
    """解析高德逆地理编码响应。"""
    regeocode_raw = data.get("regeocode", {})
    regeocode: dict[str, Any] = (
        regeocode_raw if isinstance(regeocode_raw, dict) else {}
    )
    addr_raw = regeocode.get("addressComponent", {})
    addr: dict[str, Any] = addr_raw if isinstance(addr_raw, dict) else {}
    aois_raw = regeocode.get("aois", [])
    aois = aois_raw if isinstance(aois_raw, list) else []
    pois_raw = regeocode.get("pois", [])
    pois = pois_raw if isinstance(pois_raw, list) else []
    formatted_address_raw = regeocode.get("formatted_address", "")
    formatted_address = (
        formatted_address_raw
        if isinstance(formatted_address_raw, str)
        else ""
    )
    adcode = _clean_optional_string(addr.get("adcode"))

    address = _select_regeocode_address(regeocode, addr, aois, pois, formatted_address)

    if address is None and adcode is None:
        return None

    return AmapRegeoResult(address=address, adcode=adcode)


def _select_regeocode_address(
    _regeocode: dict[str, Any],
    addr: dict[str, Any],
    aois: list[Any],
    pois: list[Any],
    formatted_address: str,
) -> str | None:
    """按 AOI、POI、格式化地址顺序选择最适合通知展示的地址。"""
    district = _clean_optional_string(addr.get("district")) or ""

    # 优先级1: AOI（兴趣面）- 当坐标在区域内部时最准确
    for aoi in aois:
        if not isinstance(aoi, dict):
            continue
        aoi_name = _clean_optional_string(aoi.get("name"))
        aoi_distance = aoi.get("distance", "")
        if aoi_name is None:
            continue

        try:
            dist_value = float(aoi_distance)
        except (ValueError, TypeError):
            continue

        # 只有在区域内部（distance=0）才使用 AOI，避免把附近园区误认为当前位置。
        if dist_value == 0:
            return f"{district}{aoi_name}"

    # 优先级2: POI（兴趣点）- 100米内的兴趣点
    for poi in pois:
        if not isinstance(poi, dict):
            continue
        poi_name = _clean_optional_string(poi.get("name"))
        poi_distance = poi.get("distance", "")
        if poi_name is None:
            continue

        try:
            dist_value = float(poi_distance)
        except (ValueError, TypeError):
            continue

        if dist_value <= 100:
            return f"{district}{poi_name}"

    # 优先级3: formatted_address（兜底），去掉省市前缀保留区县及以下。
    if not formatted_address:
        return None

    province = _clean_optional_string(addr.get("province")) or ""
    city = _clean_optional_string(addr.get("city")) or ""
    result = formatted_address
    if province and result.startswith(province):
        result = result[len(province):]
    if city and result.startswith(city):
        result = result[len(city):]

    return result or None


def _build_regeocode_cache_key(latitude: float, longitude: float) -> str:
    """用约 10 米级别的坐标网格复用相近点位结果。"""
    return f"{latitude:.4f},{longitude:.4f}"


def _clean_optional_string(value: object) -> str | None:
    """清洗高德响应中可能出现的空字符串或空数组占位。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized in {"", "[]", "无"}:
        return None
    return normalized


def _is_amap_rate_limit_error(info: str, infocode: str) -> bool:
    """识别高德限流类错误，进入更长负缓存。"""
    normalized = f"{info} {infocode}".upper()
    return "CUQPS" in normalized or "DAILY_QUERY_OVER_LIMIT" in normalized
