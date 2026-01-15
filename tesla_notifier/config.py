"""配置管理"""

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    """应用配置"""

    # 数据库
    db_host: str = field(default_factory=lambda: os.getenv("DB_HOST", "localhost"))
    db_port: int = field(default_factory=lambda: int(os.getenv("DB_PORT", "5432")))
    db_name: str = field(default_factory=lambda: os.getenv("DB_NAME", "teslamate"))
    db_user: str = field(default_factory=lambda: os.getenv("DB_USER", "teslamate"))
    db_password: str = field(default_factory=lambda: os.getenv("DB_PASSWORD", ""))

    # MQTT
    mqtt_enabled: bool = field(default_factory=lambda: os.getenv("ENABLE_MQTT", "").lower() == "true")
    mqtt_url: str = field(default_factory=lambda: os.getenv("MQTT_URL", "mqtt://localhost:1883"))
    mqtt_username: str = field(default_factory=lambda: os.getenv("MQTT_USERNAME", ""))
    mqtt_password: str = field(default_factory=lambda: os.getenv("MQTT_PASSWORD", ""))

    # 定时任务
    cron_enabled: bool = field(default_factory=lambda: os.getenv("ENABLE_CRON", "").lower() == "true")
    daily_cron: str = field(default_factory=lambda: os.getenv("DAILY_CRON", "0 8 * * *"))
    weekly_cron: str = field(default_factory=lambda: os.getenv("WEEKLY_CRON", "0 9 * * 1"))
    monthly_cron: str = field(default_factory=lambda: os.getenv("MONTHLY_CRON", "0 9 1 * *"))

    # Bark 推送
    bark_url: str = field(default_factory=lambda: os.getenv("BARK_URL", "https://api.day.app"))
    bark_key: str = field(default_factory=lambda: os.getenv("BARK_KEY", ""))

    # 天气服务
    caiyun_token: str = field(default_factory=lambda: os.getenv("CAIYUN_TOKEN", ""))

    # 车辆
    car_id: str = field(default_factory=lambda: os.getenv("CAR_ID", "1"))
    min_trip_distance: float = field(
        default_factory=lambda: float(os.getenv("MIN_TRIP_DISTANCE", "1"))
    )

    # 时区
    timezone: str = field(default_factory=lambda: os.getenv("TZ", "Asia/Shanghai"))

    @property
    def db_dsn(self) -> str:
        """PostgreSQL 连接字符串"""
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def mqtt_host(self) -> str:
        """解析 MQTT 主机"""
        url = self.mqtt_url.replace("mqtt://", "").replace("mqtts://", "")
        return url.split(":")[0]

    @property
    def mqtt_port(self) -> int:
        """解析 MQTT 端口"""
        url = self.mqtt_url.replace("mqtt://", "").replace("mqtts://", "")
        parts = url.split(":")
        return int(parts[1]) if len(parts) > 1 else 1883


config = Config()
