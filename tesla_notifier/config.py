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
    mqtt_enabled: bool = field(
        default_factory=lambda: os.getenv("ENABLE_MQTT", "").lower() == "true"
    )
    mqtt_url: str = field(
        default_factory=lambda: os.getenv("MQTT_URL", "mqtt://localhost:1883")
    )
    mqtt_username: str = field(default_factory=lambda: os.getenv("MQTT_USERNAME", ""))
    mqtt_password: str = field(default_factory=lambda: os.getenv("MQTT_PASSWORD", ""))

    # 定时任务
    cron_enabled: bool = field(
        default_factory=lambda: os.getenv("ENABLE_CRON", "").lower() == "true"
    )
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
    grafana_base_url: str = field(default_factory=lambda: os.getenv("GRAFANA_BASE_URL", ""))

    # 高德地图
    amap_key: str = field(default_factory=lambda: os.getenv("AMAP_KEY", ""))
    traffic_analysis_enabled: bool = field(
        default_factory=lambda: os.getenv("TRAFFIC_ANALYSIS_ENABLED", "").upper()
        == "ON"
    )
    traffic_sample_interval: int = field(
        default_factory=lambda: int(os.getenv("TRAFFIC_SAMPLE_INTERVAL", "300"))
    )
    traffic_sample_min_distance_km: float = field(
        default_factory=lambda: float(
            os.getenv("TRAFFIC_SAMPLE_MIN_DISTANCE_KM", "3")
        )
    )
    traffic_query_radius: int = field(
        default_factory=lambda: int(os.getenv("TRAFFIC_QUERY_RADIUS", "1000"))
    )

    # 车辆
    car_id: str = field(default_factory=lambda: os.getenv("CAR_ID", "1"))
    min_trip_distance: float = field(
        default_factory=lambda: float(os.getenv("MIN_TRIP_DISTANCE", "1"))
    )
    trip_compensation_interval: int = field(
        default_factory=lambda: int(os.getenv("TRIP_COMPENSATION_INTERVAL", "300"))
    )
    trip_offline_reconcile_delay: int = field(
        default_factory=lambda: int(os.getenv("TRIP_OFFLINE_RECONCILE_DELAY", "960"))
    )
    trip_compensation_max_age_hours: int = field(
        default_factory=lambda: int(os.getenv("TRIP_COMPENSATION_MAX_AGE_HOURS", "24"))
    )

    # 时区
    timezone: str = field(default_factory=lambda: os.getenv("TZ", "Asia/Shanghai"))

    # 哨兵录制检测
    sentry_notify_enabled: bool = field(
        default_factory=lambda: os.getenv("SENTRY_NOTIFY_ENABLED", "").upper() == "ON"
    )
    sentry_recording_cooldown: int = field(
        default_factory=lambda: int(os.getenv("SENTRY_RECORDING_COOLDOWN", "300"))
    )

    # 离车安全提醒
    departure_safety_notify_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "DEPARTURE_SAFETY_NOTIFY_ENABLED", ""
        ).upper()
        == "ON"
    )
    departure_safety_delay: int = field(
        default_factory=lambda: int(os.getenv("DEPARTURE_SAFETY_DELAY", "180"))
    )
    departure_safety_cooldown: int = field(
        default_factory=lambda: int(os.getenv("DEPARTURE_SAFETY_COOLDOWN", "600"))
    )

    # 胎压提醒
    tpms_notify_enabled: bool = field(
        default_factory=lambda: os.getenv("TPMS_NOTIFY_ENABLED", "").upper() == "ON"
    )
    tpms_notify_cooldown: int = field(
        default_factory=lambda: int(os.getenv("TPMS_NOTIFY_COOLDOWN", "1800"))
    )

    # 充电异常提醒
    charging_issue_notify_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "CHARGING_ISSUE_NOTIFY_ENABLED", ""
        ).upper()
        == "ON"
    )
    charging_issue_cooldown: int = field(
        default_factory=lambda: int(os.getenv("CHARGING_ISSUE_COOLDOWN", "900"))
    )
    charging_no_power_grace_period: int = field(
        default_factory=lambda: int(
            os.getenv("CHARGING_NO_POWER_GRACE_PERIOD", "180")
        )
    )
    charging_stopped_min_soc_gap: int = field(
        default_factory=lambda: int(os.getenv("CHARGING_STOPPED_MIN_SOC_GAP", "3"))
    )

    # 系统健康通知
    system_health_notify_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "SYSTEM_HEALTH_NOTIFY_ENABLED", "ON"
        ).upper()
        == "ON"
    )
    failure_alert_notify_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "FAILURE_ALERT_NOTIFY_ENABLED", "ON"
        ).upper()
        == "ON"
    )
    db_failure_alert_threshold: int = field(
        default_factory=lambda: int(os.getenv("DB_FAILURE_ALERT_THRESHOLD", "3"))
    )
    mqtt_disconnect_alert_after: int = field(
        default_factory=lambda: int(os.getenv("MQTT_DISCONNECT_ALERT_AFTER", "300"))
    )
    mqtt_freshness_monitor_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "MQTT_FRESHNESS_MONITOR_ENABLED",
            "ON",
        ).upper()
        == "ON"
    )
    mqtt_freshness_check_interval: int = field(
        default_factory=lambda: int(os.getenv("MQTT_FRESHNESS_CHECK_INTERVAL", "300"))
    )
    mqtt_freshness_stale_after: int = field(
        default_factory=lambda: int(os.getenv("MQTT_FRESHNESS_STALE_AFTER", "900"))
    )
    mqtt_freshness_db_active_window: int = field(
        default_factory=lambda: int(
            os.getenv("MQTT_FRESHNESS_DB_ACTIVE_WINDOW", "1800")
        )
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

        if self.trip_compensation_interval < 60:
            errors.append("TRIP_COMPENSATION_INTERVAL 建议不小于 60 秒")

        if self.trip_offline_reconcile_delay < 60:
            errors.append("TRIP_OFFLINE_RECONCILE_DELAY 建议不小于 60 秒")

        if self.trip_compensation_max_age_hours < 1:
            errors.append("TRIP_COMPENSATION_MAX_AGE_HOURS 必须大于等于 1")

        if self.departure_safety_delay < 30:
            errors.append("DEPARTURE_SAFETY_DELAY 建议不小于 30 秒")

        if self.departure_safety_cooldown < 60:
            errors.append("DEPARTURE_SAFETY_COOLDOWN 建议不小于 60 秒")

        if self.charging_no_power_grace_period < 30:
            errors.append("CHARGING_NO_POWER_GRACE_PERIOD 建议不小于 30 秒")

        if self.db_failure_alert_threshold < 1:
            errors.append("DB_FAILURE_ALERT_THRESHOLD 必须大于等于 1")

        if self.mqtt_disconnect_alert_after < 30:
            errors.append("MQTT_DISCONNECT_ALERT_AFTER 建议不小于 30 秒")

        if self.mqtt_freshness_check_interval < 60:
            errors.append("MQTT_FRESHNESS_CHECK_INTERVAL 建议不小于 60 秒")

        if self.mqtt_freshness_stale_after < 300:
            errors.append("MQTT_FRESHNESS_STALE_AFTER 建议不小于 300 秒")

        if self.mqtt_freshness_db_active_window < self.mqtt_freshness_stale_after:
            errors.append(
                "MQTT_FRESHNESS_DB_ACTIVE_WINDOW "
                "必须大于等于 MQTT_FRESHNESS_STALE_AFTER"
            )

        return errors


config = Config()
