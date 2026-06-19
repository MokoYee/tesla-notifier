"""Grafana 深链生成模块。"""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("grafana")

DRIVE_DETAILS_PATH = "/d/zm7wN6Zgz/drive-details"
CHARGE_DETAILS_PATH = "/d/BHhxFeZRz/charge-details"
DEFAULT_LINK_PADDING = timedelta(minutes=5)


def build_drive_details_url(
    *,
    drive_id: int,
    car_id: int,
    start_time: str,
    end_time: str,
) -> str | None:
    """生成 TeslaMate 行程详情 Grafana 链接。"""
    return _build_details_url(
        path=DRIVE_DETAILS_PATH,
        car_id=car_id,
        start_time=start_time,
        end_time=end_time,
        extra_params={"var-drive_id": str(drive_id)},
    )


def build_charge_details_url(
    *,
    charging_process_id: int,
    car_id: int,
    start_time: str,
    end_time: str,
) -> str | None:
    """生成 TeslaMate 充电详情 Grafana 链接。"""
    return _build_details_url(
        path=CHARGE_DETAILS_PATH,
        car_id=car_id,
        start_time=start_time,
        end_time=end_time,
        extra_params={"var-charging_process_id": str(charging_process_id)},
    )


def _build_details_url(
    *,
    path: str,
    car_id: int,
    start_time: str,
    end_time: str,
    extra_params: dict[str, str],
) -> str | None:
    """按 TeslaMate dashboard 约定拼接详情页 URL。"""
    base_url = config.grafana_base_url.strip().rstrip("/")
    if not base_url:
        return None

    start_dt = _parse_iso_datetime(start_time)
    end_dt = _parse_iso_datetime(end_time)
    if start_dt is None or end_dt is None:
        logger.warning("Grafana 深链时间解析失败，跳过生成")
        return None

    params = {
        "from": str(_to_epoch_millis(start_dt - DEFAULT_LINK_PADDING)),
        "to": str(_to_epoch_millis(end_dt + DEFAULT_LINK_PADDING)),
        "var-car_id": str(car_id),
    }
    params.update(extra_params)
    return f"{base_url}{path}?{urlencode(params)}"


def _parse_iso_datetime(value: str) -> datetime | None:
    """解析 ISO 时间，统一转换到 UTC。"""
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _to_epoch_millis(value: datetime) -> int:
    """转换为 Grafana 使用的毫秒时间戳。"""
    return int(value.timestamp() * 1000)
