"""高德地图 API 模块

用于逆地理编码（坐标转地址），提供更友好的中文地址显示。
优先返回 100米内的广场、商场、小区，其次返回街道门牌号。
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

# 广场、商场关键词
PLAZA_KEYWORDS = ["广场", "商场", "购物中心", "购物", "商业街", "商业广场", "商业中心"]

# 小区关键词（通过 POI 类型判断）
COMMUNITY_KEYWORDS = ["住宅小区", "住宅区", "小区", "公寓", "花园"]

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
    """逆地理编码：返回精确地址

    优先级：
    1. 100米内的广场、商场（距离最近的）
    2. 100米内的小区（距离最近的）
    3. 区 + 乡镇 + 街道 + 门牌号

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
            "radius": "100",  # 搜索半径 100 米
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
            pois = regeocode.get("pois", [])

            district = addr.get("district", "")
            township = addr.get("township", "")

            plazas_100m = []  # 100米内的广场、商场
            communities_100m = []  # 100米内的小区

            # 分析 POI
            for poi in pois:
                name = poi.get("name", "")
                poi_type = poi.get("type", "")
                distance = poi.get("distance", "")

                if not name or name in ["[]", "无", ""]:
                    continue

                try:
                    dist_value = float(distance)
                except (ValueError, TypeError):
                    continue

                # 只处理 100米内的 POI
                if dist_value > 100:
                    continue

                # 判断是否是广场、商场
                is_plaza = any(kw in name or kw in poi_type for kw in PLAZA_KEYWORDS)
                if is_plaza:
                    plazas_100m.append((name, dist_value))

                # 判断是否是小区
                is_community = any(kw in poi_type for kw in COMMUNITY_KEYWORDS)
                if is_community:
                    communities_100m.append((name, dist_value))

            # 优先级1: 100米内的广场、商场
            if plazas_100m:
                # 选择距离最近的
                plazas_100m.sort(key=lambda x: x[1])
                name, _ = plazas_100m[0]
                return f"{district}{name}"

            # 优先级2: 100米内的小区
            if communities_100m:
                # 选择距离最近的
                communities_100m.sort(key=lambda x: x[1])
                name, _ = communities_100m[0]
                return f"{district}{name}"

            # 优先级3: 区 + 乡镇 + 街道 + 门牌号
            street_info = addr.get("streetNumber", {})

            # 处理 streetNumber 可能是 dict 或 list
            if isinstance(street_info, dict):
                street = street_info.get("street", "")
                number = street_info.get("number", "")
            else:
                street = ""
                number = ""

            # 确保是字符串
            if isinstance(street, list):
                street = ""
            if isinstance(number, list):
                number = ""

            # 如果没有街道信息，尝试从 roads 字段提取
            if not street:
                roads = regeocode.get("roads", [])
                if roads:
                    street = roads[0].get("name", "")

            # 组合地址
            parts = []
            if district:
                parts.append(district)
            if township and township != district:
                parts.append(township)
            if street and street not in ["[]", "无", ""]:
                parts.append(street)
            if number and number not in ["[]", "无", ""]:
                parts.append(number)

            result = "".join(parts) if parts else None
            return result

    except httpx.TimeoutException:
        logger.warning("高德 API 请求超时")
        return None
    except Exception as e:
        logger.exception(f"高德 API 调用异常: {e}")
        return None
