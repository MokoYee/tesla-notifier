"""高德地图 API 模块

用于逆地理编码（坐标转地址），提供更友好的中文地址显示。
优先返回 AOI 区域名称（如小区、园区、商圈），而非门牌号地址。
"""

import httpx

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("amap")

# 高德逆地理编码 API
AMAP_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"

# 请求超时（秒）
REQUEST_TIMEOUT = 10.0

# 无意义的 POI 类型，需要过滤掉
UNWANTED_POI_TYPES = {
    "公交车站", "地铁站", "停车场", "ATM", "公共厕所", "岗亭", "消防栓",
    "报刊亭", "电话亭", "自行车租赁点", "充电站", "加油站", "路灯",
    "垃圾桶", "公交站牌", "出入口", "门", "通道", "楼梯", "电梯",
}


async def reverse_geocode(latitude: float, longitude: float) -> str | None:
    """逆地理编码：返回人类友好的区域名称

    优先级：
    1. AOI（小区、园区、商圈等面状区域）
    2. 有意义的 POI（过滤掉公交站、停车场等）
    3. 社区/小区名称
    4. 区 + 街道 + 路名（兜底，不含门牌号）

    Args:
        latitude: 纬度
        longitude: 经度

    Returns:
        区域名称，失败返回 None
    """
    if not config.amap_key:
        return None

    try:
        params = {
            "key": config.amap_key,
            "location": f"{longitude},{latitude}",  # 高德格式：经度,纬度
            "extensions": "all",
            "output": "json",
            "radius": "500",  # 扩大到 500 米，覆盖更多 AOI
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

            # 1. 优先使用 AOI（小区、园区、商圈等面状区域）
            aois = regeocode.get("aois", [])
            for aoi in aois:
                name = aoi.get("name")
                if name and name not in ["[]", "无", ""]:
                    return name.strip()

            # 2. 使用 POI，但过滤掉无意义的类型
            pois = regeocode.get("pois", [])
            for poi in pois:
                name = poi.get("name")
                poi_type = poi.get("type", "")

                if not name or name in ["[]", "无", ""]:
                    continue

                # 跳过无意义的 POI 类型
                if any(unwanted in poi_type for unwanted in UNWANTED_POI_TYPES):
                    continue

                return name.strip()

            # 3. 尝试从 addressComponent 获取小区/社区名
            addr = regeocode.get("addressComponent", {})
            neighborhood = addr.get("neighborhood", {})
            if isinstance(neighborhood, dict):
                nb_name = neighborhood.get("name")
                if nb_name and nb_name not in ["[]", "无", ""]:
                    return nb_name.strip()

            # 4. 兜底：返回区 + 街道 + 路名（不含门牌号）
            district = addr.get("district", "")
            township = addr.get("township", "")
            street = ""
            street_info = addr.get("streetNumber")
            if isinstance(street_info, dict):
                street = street_info.get("street", "")

            parts = [part for part in [district, township, street] if part]
            if parts:
                return "".join(parts)

            return None

    except httpx.TimeoutException:
        logger.warning("高德 API 请求超时")
        return None
    except Exception as e:
        logger.exception(f"高德 API 调用异常: {e}")
        return None
