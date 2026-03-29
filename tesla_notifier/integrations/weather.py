"""天气服务模块"""

from dataclasses import dataclass
from typing import Literal

import httpx

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("weather")


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
    aqi: int | None = None
    pm25: float | None = None
    ultraviolet: float | None = None
    source: Literal["caiyun", "openmeteo"] = "openmeteo"


# 彩云天气 skycon 代码映射
CAIYUN_SKYCON: dict[str, str] = {
    "CLEAR_DAY": "晴",
    "CLEAR_NIGHT": "晴",
    "PARTLY_CLOUDY_DAY": "多云",
    "PARTLY_CLOUDY_NIGHT": "多云",
    "CLOUDY": "阴",
    "LIGHT_HAZE": "轻度雾霾",
    "MODERATE_HAZE": "中度雾霾",
    "HEAVY_HAZE": "重度雾霾",
    "LIGHT_RAIN": "小雨",
    "MODERATE_RAIN": "中雨",
    "HEAVY_RAIN": "大雨",
    "STORM_RAIN": "暴雨",
    "FOG": "雾",
    "LIGHT_SNOW": "小雪",
    "MODERATE_SNOW": "中雪",
    "HEAVY_SNOW": "大雪",
    "STORM_SNOW": "暴雪",
    "DUST": "浮尘",
    "SAND": "沙尘",
    "WIND": "大风",
}

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


async def get_weather_from_caiyun(latitude: float, longitude: float) -> WeatherData | None:
    """从彩云天气获取数据"""
    if not config.caiyun_token:
        logger.warning("彩云天气 Token 未配置，跳过")
        return None

    location = f"{longitude},{latitude}"
    url = f"https://api.caiyunapp.com/v2.6/{config.caiyun_token}/{location}/weather"
    params = {"alert": "true", "dailysteps": "1"}

    logger.info(f"请求彩云天气 API: {location}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)

            if response.status_code != 200:
                logger.error(f"彩云 API 请求失败: HTTP {response.status_code}")
                return None

            data = response.json()

            if data.get("status") != "ok":
                logger.error(f"彩云 API 返回错误: {data.get('status')}")
                return None

            realtime = data["result"]["realtime"]
            daily = data["result"]["daily"]

            return WeatherData(
                condition=CAIYUN_SKYCON.get(realtime["skycon"], realtime["skycon"]),
                temp=realtime["temperature"],
                temp_min=daily["temperature"][0].get("min", realtime["temperature"] - 5),
                temp_max=daily["temperature"][0].get("max", realtime["temperature"] + 5),
                humidity=int(realtime["humidity"] * 100),
                wind_speed=realtime["wind"]["speed"],
                precipitation=daily["precipitation"][0].get("avg"),
                aqi=realtime.get("air_quality", {}).get("aqi", {}).get("chn"),
                pm25=realtime.get("air_quality", {}).get("pm25"),
                ultraviolet=realtime.get("life_index", {}).get("ultraviolet", {}).get("index"),
                source="caiyun",
            )

    except Exception as e:
        logger.exception(f"彩云天气 API 异常: {e}")
        return None


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
        async with httpx.AsyncClient(timeout=30.0) as client:
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
    """获取天气数据，优先彩云，失败回退 Open-Meteo"""
    logger.info(f"获取天气数据: {latitude}, {longitude}")

    if config.caiyun_token:
        caiyun_data = await get_weather_from_caiyun(latitude, longitude)
        if caiyun_data:
            return caiyun_data
        logger.warning("彩云天气获取失败，回退到 Open-Meteo")

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

    # 空气质量建议（彩云特有）
    if weather.aqi is not None:
        if weather.aqi > 150:
            suggestions.append(f"空气质量较差(AQI:{weather.aqi})，建议开启车内空气净化")
        elif weather.aqi > 100:
            suggestions.append(f"空气质量一般(AQI:{weather.aqi})")

    # 降水建议
    if weather.precipitation and weather.precipitation > 10:
        suggestions.append("有降雨，注意行车安全，能见度可能降低")

    # 风速建议
    if weather.wind_speed and weather.wind_speed > 30:
        suggestions.append("风力较大，高速行驶注意横风影响")

    # 紫外线建议（彩云特有）
    if weather.ultraviolet is not None and weather.ultraviolet > 7:
        suggestions.append("紫外线较强，建议使用遮阳挡")

    return "；".join(suggestions) if suggestions else "天气良好，祝您出行愉快"


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

