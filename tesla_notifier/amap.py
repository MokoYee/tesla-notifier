"""高德地图 API 模块

用于逆地理编码（坐标转地址），提供更友好的中文地址显示。

地址解析优先级：
1. AOI（兴趣面）：当前所在的区域（如物流园、商场、小区等）
2. 最近的 POI（兴趣点）：优先使用 POI 的 address 字段（更精确）
3. formatted_address：高德返回的格式化地址（兜底）
"""

import math

import httpx

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("amap")

# 高德逆地理编码 API
AMAP_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"

# 请求超时（秒）
REQUEST_TIMEOUT = 10.0

# WGS-84 转 GCJ-02 坐标转换常量
_A = 6378245.0  # 长半轴
_EE = 0.00669342162296594323  # 偏心率平方


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
    1. 最近的 POI（兴趣点）：优先使用 address 字段（最精确，如"环通物流园7-A07"）
    2. AOI（兴趣面）：当前所在的区域（如"环通物流园"）
    3. formatted_address：高德返回的格式化地址（兜底）

    Args:
        latitude: WGS-84 纬度
        longitude: WGS-84 经度

    Returns:
        地址字符串，失败返回 None
    """
    if not config.amap_key:
        return None

    try:
        # WGS-84 转 GCJ-02（高德地图使用火星坐标系）
        gcj_lat, gcj_lon = _wgs84_to_gcj02(latitude, longitude)

        params = {
            "key": config.amap_key,
            "location": f"{gcj_lon},{gcj_lat}",  # 高德格式：经度,纬度
            "extensions": "all",
            "output": "json",
            "radius": "200",  # 搜索半径 200 米
        }

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(AMAP_REGEO_URL, params=params)

            if response.status_code != 200:
                logger.warning(f"高德 API 请求失败: HTTP {response.status_code}")
                return None

            data = response.json()

            if data.get("status") != "1":
                logger.warning(f"高德 API 返回错误: {data.get('info', '未知错误')}")
                return None

            regeocode = data.get("regeocode", {})
            addr = regeocode.get("addressComponent", {})
            aois = regeocode.get("aois", [])
            pois = regeocode.get("pois", [])
            formatted_address = regeocode.get("formatted_address", "")

            # 获取区县名称（用于组合地址）
            district = addr.get("district", "")

            # 优先级1: 最近的 POI（兴趣点）- 最精确
            # POI 列表已按距离排序，取第一个有效的
            for poi in pois:
                poi_name = poi.get("name", "")
                poi_address = poi.get("address", "")
                poi_distance = poi.get("distance", "")

                if not poi_name or poi_name in ["[]", "无", ""]:
                    continue

                try:
                    dist_value = float(poi_distance)
                except (ValueError, TypeError):
                    continue

                # 只使用 100 米内的 POI
                if dist_value > 100:
                    continue

                # 优先使用 POI 的 address 字段
                if poi_address and poi_address not in ["[]", "无", ""]:
                    return f"{district}{poi_address}"

                # 没有 address 则使用 name
                return f"{district}{poi_name}"

            # 优先级2: AOI（兴趣面）- 当前所在的区域
            # distance=0 表示坐标点在该区域内部
            for aoi in aois:
                aoi_name = aoi.get("name", "")
                aoi_distance = aoi.get("distance", "")

                if not aoi_name or aoi_name in ["[]", "无", ""]:
                    continue

                try:
                    dist_value = float(aoi_distance)
                except (ValueError, TypeError):
                    continue

                # 在区域内部（distance=0）或非常近（<50米）
                if dist_value <= 50:
                    return f"{district}{aoi_name}"

            # 优先级3: formatted_address（兜底）
            # 去掉省市前缀，保留区县及以下
            if formatted_address:
                province = addr.get("province", "")
                city = addr.get("city", "")

                result = formatted_address
                # 去掉省份前缀
                if province and result.startswith(province):
                    result = result[len(province):]
                # 去掉城市前缀
                if city and result.startswith(city):
                    result = result[len(city):]

                if result:
                    return result

            return None

    except httpx.TimeoutException:
        logger.warning("高德 API 请求超时")
        return None
    except Exception as e:
        logger.exception(f"高德 API 调用异常: {e}")
        return None
