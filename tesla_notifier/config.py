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
    weekly_cron: str = field(default_factory=lambda: os.getenv("WEEKLY_CRON", "0 9 * * mon"))
    monthly_cron: str = field(default_factory=lambda: os.getenv("MONTHLY_CRON", "0 9 1 * *"))

    # Bark 推送
    bark_url: str = field(default_factory=lambda: os.getenv("BARK_URL", "https://api.day.app"))
    bark_key: str = field(default_factory=lambda: os.getenv("BARK_KEY", ""))
    bark_icon: str = field(
        default_factory=lambda: os.getenv(
            "BARK_ICON",
            "https://static.vecteezy.com/system/resources/previews/020/975/563/non_2x/tesla-logo-tesla-icon-transparent-free-png.png"
        )
    )

    # 天气服务
    caiyun_token: str = field(default_factory=lambda: os.getenv("CAIYUN_TOKEN", ""))

    # 高德地图
    amap_key: str = field(default_factory=lambda: os.getenv("AMAP_KEY", ""))

    # 车辆
    car_id: str = field(default_factory=lambda: os.getenv("CAR_ID", "1"))
    min_trip_distance: float = field(
        default_factory=lambda: float(os.getenv("MIN_TRIP_DISTANCE", "1"))
    )

    # 时区
    timezone: str = field(default_factory=lambda: os.getenv("TZ", "Asia/Shanghai"))

    # 哨兵录制检测
    sentry_notify_enabled: bool = field(
        default_factory=lambda: os.getenv("SENTRY_NOTIFY_ENABLED", "").upper() == "ON"
    )
    sentry_battery_drop_threshold: float = field(
        default_factory=lambda: float(os.getenv("SENTRY_BATTERY_DROP_THRESHOLD", "0.15"))
    )
    sentry_recording_cooldown: int = field(
        default_factory=lambda: int(os.getenv("SENTRY_RECORDING_COOLDOWN", "300"))
    )
    sentry_power_threshold: float = field(
        default_factory=lambda: float(os.getenv("SENTRY_POWER_THRESHOLD", "50"))
    )

    # 日志级别（DEBUG/INFO/WARNING/ERROR）
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )

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
        try:
            return int(parts[1]) if len(parts) > 1 else 1883
        except ValueError:
            return 1883

    def validate(self) -> list[str]:
        """验证配置，返回错误列表

        在应用启动时调用，检查必需的配置项是否已正确设置。
        """
        errors: list[str] = []

        # 如果启用了 MQTT，检查 MQTT 配置
        if self.mqtt_enabled:
            if not self.mqtt_url:
                errors.append("MQTT_URL 未配置（ENABLE_MQTT=true 时必需）")

        # 如果启用了定时任务，检查 cron 表达式格式
        if self.cron_enabled:
            for name, expr in [
                ("DAILY_CRON", self.daily_cron),
                ("WEEKLY_CRON", self.weekly_cron),
                ("MONTHLY_CRON", self.monthly_cron),
            ]:
                parts = expr.split()
                if len(parts) != 5:
                    errors.append(f"{name} 格式错误: 需要 5 个字段，实际 {len(parts)} 个")

        # Bark 推送密钥是必需的
        if not self.bark_key:
            errors.append("BARK_KEY 未配置（必需）")

        return errors


config = Config()
