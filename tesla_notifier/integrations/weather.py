"""天气服务模块"""

from dataclasses import dataclass
from typing import Literal

import httpx

from tesla_notifier.config import config
from tesla_notifier.integrations.amap import amap_request_json, get_adcode
from tesla_notifier.logger import setup_logger

logger = setup_logger("weather")

AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
WEATHER_REQUEST_TIMEOUT = 30.0


@dataclass
class WeatherData:
    """天气数据"""

    condition: str
    temp: float
    temp_min: float
    temp_max: float
    humidity: int
    wind_speed: float | None = None
    precipitation: float | None = None
    source: Literal["amap", "openmeteo"] = "openmeteo"

# WMO Weather interpretation codes
WMO_CODES: dict[int, str] = {
    0: "晴朗",
    1: "晴朗",
    2: "多云",
    3: "阴天",
    45: "雾",
    48: "雾凇",
    51: "小雨",
    53: "中雨",
    55: "大雨",
    56: "冻雨",
    57: "冻雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨",
    81: "阵雨",
    82: "暴雨",
    85: "阵雪",
    86: "阵雪",
    95: "雷暴",
    96: "雷暴伴冰雹",
    99: "雷暴伴冰雹",
}


async def get_weather_from_amap(latitude: float, longitude: float) -> WeatherData | None:
    """从高德天气获取数据。"""
    if not config.amap_key:
        return None

    adcode = await get_adcode(latitude, longitude)
    if not adcode:
        logger.warning("高德天气缺少 adcode，回退 Open-Meteo")
        return None

    params = {
        "key": config.amap_key,
        "city": adcode,
        "extensions": "all",
        "output": "json",
    }
    logger.info(f"请求高德天气 API: adcode={adcode}")

    data = await amap_request_json(
        AMAP_WEATHER_URL,
        params,
        "高德天气",
        timeout=WEATHER_REQUEST_TIMEOUT,
    )
    if data is None:
        return None

    if data.get("status") != "1":
        logger.warning(
            "高德天气返回错误: "
            f"{data.get('info', '未知错误')} ({data.get('infocode', 'N/A')})"
        )
        return None

    try:
        return _parse_amap_weather(data)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning(f"高德天气返回数据无法解析: {exc}")
        return None


def _parse_amap_weather(data: dict[str, object]) -> WeatherData:
    """解析高德天气返回结果，优先使用预报并补齐当前字段。"""
    forecasts = data.get("forecasts", [])
    if not isinstance(forecasts, list) or not forecasts:
        raise ValueError("forecasts 为空")

    forecast = forecasts[0]
    if not isinstance(forecast, dict):
        raise ValueError("forecast 格式异常")

    casts = forecast.get("casts", [])
    if not isinstance(casts, list) or not casts:
        raise ValueError("casts 为空")

    today = casts[0]
    if not isinstance(today, dict):
        raise ValueError("cast 格式异常")

    lives = data.get("lives", [])
    live = lives[0] if isinstance(lives, list) and lives else {}
    live = live if isinstance(live, dict) else {}

    day_weather = _as_str(today.get("dayweather")) or "未知"
    night_weather = _as_str(today.get("nightweather"))
    condition = day_weather
    if night_weather and night_weather != day_weather:
        condition = f"{day_weather}转{night_weather}"

    temp_max = _as_float(today.get("daytemp"))
    temp_min = _as_float(today.get("nighttemp"))
    live_temp = _as_optional_float(live.get("temperature"))
    temp = live_temp if live_temp is not None else (temp_min + temp_max) / 2
    humidity = _as_int(live.get("humidity"), default=50)
    wind_speed = _wind_power_to_speed_kmh(
        _as_str(live.get("windpower")) or _as_str(today.get("daypower"))
    )

    return WeatherData(
        condition=condition,
        temp=temp,
        temp_min=temp_min,
        temp_max=temp_max,
        humidity=humidity,
        wind_speed=wind_speed,
        source="amap",
    )


async def get_weather_from_openmeteo(latitude: float, longitude: float) -> WeatherData | None:
    """从 Open-Meteo 获取数据（备用）"""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": str(latitude),
        "longitude": str(longitude),
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "Asia/Shanghai",
        "forecast_days": "1",
    }

    logger.info(f"请求 Open-Meteo API (备用): {latitude}, {longitude}")

    try:
        async with httpx.AsyncClient(timeout=WEATHER_REQUEST_TIMEOUT) as client:
            response = await client.get(url, params=params)

            if response.status_code != 200:
                logger.error(f"Open-Meteo API 请求失败: HTTP {response.status_code}")
                return None

            data = response.json()

            return WeatherData(
                condition=WMO_CODES.get(data["current"]["weather_code"], "未知"),
                temp=data["current"]["temperature_2m"],
                temp_min=data["daily"]["temperature_2m_min"][0],
                temp_max=data["daily"]["temperature_2m_max"][0],
                humidity=data["current"]["relative_humidity_2m"],
                wind_speed=data["current"]["wind_speed_10m"],
                precipitation=data["daily"]["precipitation_sum"][0],
                source="openmeteo",
            )

    except Exception as e:
        logger.exception(f"Open-Meteo API 异常: {e}")
        return None


async def get_weather(latitude: float, longitude: float) -> WeatherData | None:
    """获取天气数据，优先高德，失败回退 Open-Meteo。"""
    logger.info(f"获取天气数据: {latitude}, {longitude}")

    if config.amap_key:
        amap_data = await get_weather_from_amap(latitude, longitude)
        if amap_data:
            return amap_data
        logger.warning("高德天气获取失败，回退到 Open-Meteo")

    return await get_weather_from_openmeteo(latitude, longitude)


def generate_weather_suggestion(weather: WeatherData) -> str:
    """生成天气建议"""
    suggestions: list[str] = []

    # 温度建议
    if weather.temp < 0:
        suggestions.append("气温较低，注意预热车辆，能耗可能增加")
    elif weather.temp > 35:
        suggestions.append("高温天气，空调能耗较高，建议提前规划充电")
    elif 15 <= weather.temp <= 25:
        suggestions.append("温度适宜，能耗表现较好")

    # 降水建议
    if weather.precipitation and weather.precipitation > 10:
        suggestions.append("有降雨，注意行车安全，能见度可能降低")

    # 风速建议
    if weather.wind_speed and weather.wind_speed > 30:
        suggestions.append("风力较大，高速行驶注意横风影响")

    return "；".join(suggestions) if suggestions else "天气良好，祝您出行愉快"


def _as_str(value: object) -> str | None:
    """把接口返回值清洗成非空字符串。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized in {"", "[]", "无"}:
        return None
    return normalized


def _as_float(value: object) -> float:
    """把接口返回值转成浮点数，缺失时抛出异常交给上层降级。"""
    if isinstance(value, bool):
        raise ValueError("布尔值不是有效数值")
    if isinstance(value, int | float | str):
        return float(value)
    raise ValueError(f"无效数值: {value!r}")


def _as_optional_float(value: object) -> float | None:
    """把可选接口返回值转成浮点数。"""
    if value is None:
        return None
    try:
        return _as_float(value)
    except ValueError:
        return None


def _as_int(value: object, default: int) -> int:
    """把接口返回值转成整数，缺失时使用默认值。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        try:
            return int(float(value))
        except ValueError:
            return default
    return default


def _wind_power_to_speed_kmh(wind_power: str | None) -> float | None:
    """把高德风力等级粗略换算为 km/h，供现有风速提醒复用。"""
    if wind_power is None:
        return None

    normalized = wind_power.replace("级", "").strip()
    if normalized in {"≤3", "<3", "3以下", "微风"}:
        return 19.0

    for separator in ("-", "~", "～"):
        if separator in normalized:
            upper = normalized.split(separator)[-1]
            return _wind_level_to_kmh(_as_int(upper, default=0))

    return _wind_level_to_kmh(_as_int(normalized, default=0))


def _wind_level_to_kmh(level: int) -> float | None:
    """按蒲福风级上限近似转换为 km/h。"""
    if level <= 0:
        return None
    level_upper_bounds = {
        1: 5.0,
        2: 11.0,
        3: 19.0,
        4: 28.0,
        5: 38.0,
        6: 49.0,
        7: 61.0,
        8: 74.0,
        9: 88.0,
        10: 102.0,
        11: 117.0,
    }
    return level_upper_bounds.get(level, 118.0)


def get_weather_icon(condition: str) -> str:
    """根据天气状况返回对应的 emoji 图标

    Args:
        condition: 天气状况描述（如"晴"、"多云"、"雨"等）

    Returns:
        对应的 emoji 图标
    """
    # 天气状况到图标的映射
    weather_icons = {
        # 晴天
        "晴": "☀️",
        "晴朗": "☀️",
        "晴天": "☀️",

        # 多云
        "多云": "⛅",
        "少云": "🌤️",

        # 阴天
        "阴": "☁️",
        "阴天": "☁️",

        # 雨
        "小雨": "🌦️",
        "中雨": "🌧️",
        "大雨": "🌧️",
        "暴雨": "⛈️",
        "阵雨": "🌦️",
        "雷阵雨": "⛈️",
        "雷暴": "⛈️",

        # 雪
        "小雪": "🌨️",
        "中雪": "❄️",
        "大雪": "❄️",
        "暴雪": "❄️",
        "阵雪": "🌨️",
        "雪粒": "🌨️",

        # 雾霾
        "雾": "🌫️",
        "雾凇": "🌫️",
        "轻度雾霾": "😷",
        "中度雾霾": "😷",
        "重度雾霾": "😷",
        "霾": "😷",

        # 其他
        "浮尘": "🌫️",
        "沙尘": "🌫️",
        "大风": "💨",
        "冻雨": "🌧️",
        "雷暴伴冰雹": "⛈️",
    }

    # 模糊匹配：如果完全匹配失败，尝试部分匹配
    icon = weather_icons.get(condition)
    if icon:
        return icon

    # 部分匹配
    for key, value in weather_icons.items():
        if key in condition or condition in key:
            return value

    # 默认图标
    return "🌤️"
