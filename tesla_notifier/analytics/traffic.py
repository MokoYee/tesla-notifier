"""高德交通态势采样与汇总模块"""

import asyncio
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from tesla_notifier.config import config
from tesla_notifier.integrations.amap import wgs84_to_gcj02
from tesla_notifier.logger import setup_logger

logger = setup_logger("traffic")

AMAP_TRAFFIC_CIRCLE_URL = "https://restapi.amap.com/v3/traffic/status/circle"
DEFAULT_TRAFFIC_CACHE_DIR = Path("./data/traffic_snapshots")
TRAFFIC_REQUEST_TIMEOUT = 8.0
TRAFFIC_LOOP_POLL_SECONDS = 30
ACTIVE_SESSION_TTL = timedelta(hours=12)


@dataclass
class TrafficSnapshot:
    """单次交通态势采样结果"""

    sampled_at: str
    latitude: float
    longitude: float
    status: int
    status_label: str
    description: str
    expedite_pct: float
    congested_pct: float
    blocked_pct: float
    unknown_pct: float


@dataclass
class TrafficSummary:
    """行程期间的交通态势汇总"""

    traffic_label: str
    sample_count: int
    avg_expedite_pct: float
    avg_congested_pct: float
    avg_blocked_pct: float
    avg_unknown_pct: float
    high_pressure_ratio: float
    stress_index: float
    summary: str


@dataclass
class TrafficSession:
    """正在进行中的行程交通采样会话"""

    session_id: str
    started_at: str
    last_sample_at: str | None = None
    last_sample_latitude: float | None = None
    last_sample_longitude: float | None = None
    samples: list[TrafficSnapshot] = field(default_factory=list)


class TrafficSampler:
    """基于本地 JSON 的轻量路况采样器"""

    def __init__(self) -> None:
        self._cache_dir = DEFAULT_TRAFFIC_CACHE_DIR
        self._storage_available = True
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._storage_available = False
            logger.warning(f"初始化路况缓存目录失败，将降级为仅内存模式: {exc}")
        self._active_session: TrafficSession | None = None
        self._finished_summary: TrafficSummary | None = None
        self._finished_session_id: str | None = None
        self._stop_finalize_event: asyncio.Event | None = None
        self._sampling_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._sampling_inflight = False
        self._latest_position: tuple[float, float] | None = None

    @property
    def is_enabled(self) -> bool:
        """路况采样是否处于可用状态。"""
        return config.traffic_analysis_enabled and bool(config.amap_key)

    async def start_trip(self) -> None:
        """开始或恢复行程采样。"""
        if not self.is_enabled:
            return

        async with self._lock:
            if self._active_session is not None:
                return

            resumed_session = self._load_latest_active_session()
            if resumed_session is not None:
                self._active_session = resumed_session
                self._stop_finalize_event = asyncio.Event()
                logger.info(f"恢复路况采样会话: {resumed_session.session_id}")
            else:
                now = datetime.now(tz=UTC)
                session = TrafficSession(
                    session_id=now.strftime("%Y%m%dT%H%M%S"),
                    started_at=now.isoformat(),
                )
                self._active_session = session
                self._stop_finalize_event = asyncio.Event()
                self._write_session_file(session, phase="active")
                logger.info(f"创建路况采样会话: {session.session_id}")

            if self._sampling_task is None or self._sampling_task.done():
                self._sampling_task = asyncio.create_task(self._sampling_loop())

        await self.capture_snapshot(force=True)

    async def stop_trip(self) -> None:
        """结束行程采样，并生成可消费的汇总结果。"""
        if not self.is_enabled:
            return

        finalize_event = self._stop_finalize_event
        try:
            await self.capture_snapshot(force=True)

            async with self._lock:
                session = self._active_session
                self._active_session = None
                self._sampling_inflight = False
                sampling_task = self._sampling_task
                self._sampling_task = None

            if sampling_task is not None:
                sampling_task.cancel()
                await asyncio.gather(sampling_task, return_exceptions=True)

            if session is None:
                return

            summary = self._build_summary(session)
            self._finished_summary = summary
            self._finished_session_id = (
                session.session_id if summary is not None else None
            )
            self._write_session_file(session, phase="finished", summary=summary)

            if summary is None:
                logger.info("行程结束，但未采集到有效路况快照，评分不做路况修正")
                return

            logger.info(
                "行程路况汇总完成: "
                f"{summary.traffic_label}, 采样{summary.sample_count}次, "
                f"压力指数{summary.stress_index:.1f}"
            )
        finally:
            if finalize_event is not None and not finalize_event.is_set():
                finalize_event.set()

    async def shutdown(self) -> None:
        """服务关闭时停止后台采样，但不把未结束行程标记为完成。"""
        async with self._lock:
            sampling_task = self._sampling_task
            self._sampling_task = None

        if sampling_task is not None:
            sampling_task.cancel()
            await asyncio.gather(sampling_task, return_exceptions=True)

    async def discard_active_trip(self) -> None:
        """丢弃未正常结束的路况采样会话，避免离线超时后串到下一趟行程。"""
        async with self._lock:
            session = self._active_session
            self._active_session = None
            self._sampling_inflight = False
            sampling_task = self._sampling_task
            self._sampling_task = None
            finalize_event = self._stop_finalize_event
            self._stop_finalize_event = None

        if sampling_task is not None:
            sampling_task.cancel()
            await asyncio.gather(sampling_task, return_exceptions=True)

        if session is not None:
            logger.info(f"已丢弃未完成的路况采样会话: {session.session_id}")
            self._delete_session_file(session.session_id)
        else:
            stale_session = self._load_latest_active_session()
            if stale_session is not None:
                logger.info(f"已清理遗留的路况采样会话: {stale_session.session_id}")
                self._delete_session_file(stale_session.session_id)

        if finalize_event is not None and not finalize_event.is_set():
            finalize_event.set()

    async def update_position(
        self,
        latitude: float | None,
        longitude: float | None,
    ) -> None:
        """更新最新坐标，供后台采样循环读取。"""
        if latitude is None or longitude is None:
            return

        self._latest_position = (latitude, longitude)

    async def capture_snapshot(self, force: bool = False) -> None:
        """在满足阈值时抓取一次交通态势快照。"""
        if not self.is_enabled or self._latest_position is None:
            return

        async with self._lock:
            session = self._active_session
            if session is None or self._sampling_inflight:
                return

            latitude, longitude = self._latest_position
            now = datetime.now(tz=UTC)
            if not force and not self._should_capture(session, now, latitude, longitude):
                return

            session_id = session.session_id
            self._sampling_inflight = True

        snapshot = await fetch_traffic_snapshot(latitude, longitude)

        async with self._lock:
            self._sampling_inflight = False
            session = self._active_session
            if session is None or session.session_id != session_id or snapshot is None:
                return

            session.samples.append(snapshot)
            session.last_sample_at = snapshot.sampled_at
            session.last_sample_latitude = snapshot.latitude
            session.last_sample_longitude = snapshot.longitude
            self._write_session_file(session, phase="active")

        logger.info(
            "记录路况采样: "
            f"{snapshot.status_label}, 缓行{snapshot.congested_pct:.0f}%, "
            f"拥堵{snapshot.blocked_pct:.0f}%"
        )

    async def consume_finished_summary(self) -> TrafficSummary | None:
        """消费最近一次已完成行程的路况汇总。"""
        if self._finished_summary is not None:
            finished_summary = self._finished_summary
            session_id = self._finished_session_id
            self._finished_summary = None
            self._finished_session_id = None
            if session_id is not None:
                self._delete_session_file(session_id)
            return finished_summary

        summary_file = self._find_latest_finished_file()
        if summary_file is None:
            return None

        file_summary: TrafficSummary | None
        try:
            payload = json.loads(summary_file.read_text(encoding="utf-8"))
            file_summary = self._summary_from_payload(payload)
        except Exception as exc:
            logger.warning(f"读取路况汇总失败，已忽略: {exc}")
            file_summary = None
        finally:
            try:
                summary_file.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(f"删除路况缓存失败: {exc}")

        return file_summary

    async def wait_for_stop_finalize(self, timeout: float = 9.0) -> None:
        """等待行程停止后的路况汇总完成，避免主推送抢跑。"""
        finalize_event = self._stop_finalize_event
        if finalize_event is None or finalize_event.is_set():
            return

        try:
            await asyncio.wait_for(finalize_event.wait(), timeout=timeout)
        except TimeoutError:
            logger.warning("等待路况汇总超时，本次主推送将按无路况增强继续")

    async def _sampling_loop(self) -> None:
        """后台低频采样循环。"""
        try:
            while True:
                await asyncio.sleep(TRAFFIC_LOOP_POLL_SECONDS)
                await self.capture_snapshot(force=False)
                if self._active_session is None:
                    return
        except asyncio.CancelledError:
            logger.debug("路况采样循环已停止")
            raise

    def _should_capture(
        self,
        session: TrafficSession,
        now: datetime,
        latitude: float,
        longitude: float,
    ) -> bool:
        """基于时间与位移阈值决定是否采样。"""
        if session.last_sample_at is None:
            return True

        last_sample_at = datetime.fromisoformat(session.last_sample_at)
        elapsed_seconds = (now - last_sample_at).total_seconds()
        if elapsed_seconds >= config.traffic_sample_interval:
            return True

        if (
            session.last_sample_latitude is None
            or session.last_sample_longitude is None
            or elapsed_seconds < 60
        ):
            return False

        distance_km = _haversine_km(
            session.last_sample_latitude,
            session.last_sample_longitude,
            latitude,
            longitude,
        )
        return distance_km >= config.traffic_sample_min_distance_km

    def _load_latest_active_session(self) -> TrafficSession | None:
        """尝试恢复最近的未完成采样会话。"""
        if not self._storage_available:
            return None

        latest_file: Path | None = None
        latest_mtime = 0.0

        for path in self._cache_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            if payload.get("phase") != "active":
                continue

            started_at_raw = payload.get("session", {}).get("started_at")
            if not isinstance(started_at_raw, str):
                continue

            try:
                started_at = datetime.fromisoformat(started_at_raw)
            except ValueError:
                continue

            if datetime.now(tz=UTC) - started_at > ACTIVE_SESSION_TTL:
                self._delete_path(path)
                continue

            stat = path.stat()
            if stat.st_mtime > latest_mtime:
                latest_file = path
                latest_mtime = stat.st_mtime

        if latest_file is None:
            return None

        try:
            payload = json.loads(latest_file.read_text(encoding="utf-8"))
            session_payload = payload.get("session", {})
            if not isinstance(session_payload, dict):
                return None

            samples_payload = session_payload.get("samples", [])
            samples = [
                TrafficSnapshot(**sample)
                for sample in samples_payload
                if isinstance(sample, dict)
            ]
            return TrafficSession(
                session_id=str(session_payload["session_id"]),
                started_at=str(session_payload["started_at"]),
                last_sample_at=(
                    str(session_payload["last_sample_at"])
                    if session_payload.get("last_sample_at") is not None
                    else None
                ),
                last_sample_latitude=_to_optional_float(
                    session_payload.get("last_sample_latitude")
                ),
                last_sample_longitude=_to_optional_float(
                    session_payload.get("last_sample_longitude")
                ),
                samples=samples,
            )
        except Exception as exc:
            logger.warning(f"恢复路况会话失败，已忽略: {exc}")
            return None

    def _find_latest_finished_file(self) -> Path | None:
        """找到最近一次结束的会话文件。"""
        if not self._storage_available:
            return None

        latest_file: Path | None = None
        latest_mtime = 0.0

        for path in self._cache_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            if payload.get("phase") != "finished":
                continue

            stat = path.stat()
            if stat.st_mtime > latest_mtime:
                latest_file = path
                latest_mtime = stat.st_mtime

        return latest_file

    def _write_session_file(
        self,
        session: TrafficSession,
        phase: str,
        summary: TrafficSummary | None = None,
    ) -> None:
        """把当前会话写入本地 JSON 缓存。"""
        if not self._storage_available:
            return

        payload = {
            "phase": phase,
            "session": asdict(session),
            "summary": asdict(summary) if summary is not None else None,
        }
        path = self._cache_dir / f"{session.session_id}.json"
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as exc:
            self._storage_available = False
            logger.warning(f"写入路况缓存失败，后续降级为仅内存模式: {exc}")

    def _delete_session_file(self, session_id: str) -> None:
        """删除指定会话缓存文件。"""
        if not self._storage_available:
            return

        path = self._cache_dir / f"{session_id}.json"
        self._delete_path(path)

    def _delete_path(self, path: Path) -> None:
        """删除缓存文件，失败时只记录日志。"""
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"删除路况缓存失败: {exc}")

    def _summary_from_payload(self, payload: dict[str, Any]) -> TrafficSummary | None:
        """从缓存文件中解析路况汇总。"""
        summary_payload = payload.get("summary")
        if not isinstance(summary_payload, dict):
            return None

        return TrafficSummary(
            traffic_label=str(summary_payload.get("traffic_label", "")),
            sample_count=int(summary_payload.get("sample_count", 0)),
            avg_expedite_pct=float(summary_payload.get("avg_expedite_pct", 0.0)),
            avg_congested_pct=float(summary_payload.get("avg_congested_pct", 0.0)),
            avg_blocked_pct=float(summary_payload.get("avg_blocked_pct", 0.0)),
            avg_unknown_pct=float(summary_payload.get("avg_unknown_pct", 0.0)),
            high_pressure_ratio=float(summary_payload.get("high_pressure_ratio", 0.0)),
            stress_index=float(summary_payload.get("stress_index", 0.0)),
            summary=str(summary_payload.get("summary", "")),
        )

    def _build_summary(self, session: TrafficSession) -> TrafficSummary | None:
        """把多次采样汇总为评分可消费的路况画像。"""
        if not session.samples:
            return None

        sample_count = len(session.samples)
        avg_expedite = sum(sample.expedite_pct for sample in session.samples) / sample_count
        avg_congested = (
            sum(sample.congested_pct for sample in session.samples) / sample_count
        )
        avg_blocked = sum(sample.blocked_pct for sample in session.samples) / sample_count
        avg_unknown = sum(sample.unknown_pct for sample in session.samples) / sample_count
        slow_ratio = (
            sum(
                1
                for sample in session.samples
                if sample.status >= 2
                or (sample.congested_pct + sample.blocked_pct) >= 20
            )
            / sample_count
        )
        high_pressure_ratio = (
            sum(
                1
                for sample in session.samples
                if sample.status >= 3
                or sample.blocked_pct >= 8
                or (sample.congested_pct + sample.blocked_pct) >= 45
            )
            / sample_count
        )

        stress_index = _clamp(
            avg_congested * 0.55
            + avg_blocked * 1.05
            + avg_unknown * 0.2
            + slow_ratio * 6
            + high_pressure_ratio * 12
            - avg_expedite * 0.12,
            0.0,
            100.0,
        )
        traffic_label = _classify_traffic_label(
            stress_index,
            slow_ratio,
            high_pressure_ratio,
            avg_blocked,
        )
        traffic_mix = avg_congested + avg_blocked
        summary = _build_traffic_summary_sentence(
            traffic_label,
            traffic_mix,
            sample_count,
        )

        return TrafficSummary(
            traffic_label=traffic_label,
            sample_count=sample_count,
            avg_expedite_pct=round(avg_expedite, 1),
            avg_congested_pct=round(avg_congested, 1),
            avg_blocked_pct=round(avg_blocked, 1),
            avg_unknown_pct=round(avg_unknown, 1),
            high_pressure_ratio=round(high_pressure_ratio, 3),
            stress_index=round(stress_index, 1),
            summary=summary,
        )


async def fetch_traffic_snapshot(
    latitude: float,
    longitude: float,
) -> TrafficSnapshot | None:
    """调用高德交通态势接口，获取当前位置的区域路况。"""
    if not config.amap_key:
        return None

    try:
        gcj_lat, gcj_lon = wgs84_to_gcj02(latitude, longitude)
        params = {
            "key": config.amap_key,
            "location": f"{gcj_lon:.6f},{gcj_lat:.6f}",
            "radius": str(config.traffic_query_radius),
            "level": "6",
            "extensions": "base",
            "output": "json",
        }

        async with httpx.AsyncClient(timeout=TRAFFIC_REQUEST_TIMEOUT) as client:
            response = await client.get(AMAP_TRAFFIC_CIRCLE_URL, params=params)

        if response.status_code != 200:
            logger.warning(f"高德交通态势请求失败: HTTP {response.status_code}")
            return None

        data = response.json()
        if not isinstance(data, dict):
            logger.warning("高德交通态势返回格式异常")
            return None

        if data.get("status") != "1":
            logger.warning(
                "高德交通态势返回错误: "
                f"{data.get('info', '未知错误')} ({data.get('infocode', 'N/A')})"
            )
            return None

        trafficinfo_raw = data.get("trafficinfo", {})
        trafficinfo = trafficinfo_raw if isinstance(trafficinfo_raw, dict) else {}
        evaluation_raw = trafficinfo.get("evaluation", {})
        evaluation = evaluation_raw if isinstance(evaluation_raw, dict) else {}

        status = _to_int(evaluation.get("status"), default=0)
        description_value = trafficinfo.get("description", "")
        description = description_value if isinstance(description_value, str) else ""

        return TrafficSnapshot(
            sampled_at=datetime.now(tz=UTC).isoformat(),
            latitude=latitude,
            longitude=longitude,
            status=status,
            status_label=_status_label(status),
            description=description,
            expedite_pct=_to_float(evaluation.get("expedite")),
            congested_pct=_to_float(evaluation.get("congested")),
            blocked_pct=_to_float(evaluation.get("blocked")),
            unknown_pct=_to_float(evaluation.get("unknown")),
        )
    except httpx.TimeoutException:
        logger.warning("高德交通态势请求超时")
        return None
    except Exception as exc:
        logger.exception(f"高德交通态势调用异常: {exc}")
        return None


def _status_label(status: int) -> str:
    """把高德状态码映射为中文标签。"""
    return {
        0: "未知",
        1: "畅通",
        2: "缓行",
        3: "拥堵",
    }.get(status, "未知")


def _classify_traffic_label(
    stress_index: float,
    slow_ratio: float,
    high_pressure_ratio: float,
    avg_blocked_pct: float,
) -> str:
    """把采样结果归类为用户可读的交通画像。"""
    if stress_index >= 55 or high_pressure_ratio >= 0.7 or avg_blocked_pct >= 18:
        return "高压拥堵"
    if stress_index >= 28 or (slow_ratio >= 0.75 and high_pressure_ratio >= 0.2):
        return "明显拥堵"
    if stress_index >= 8 or slow_ratio >= 0.35:
        return "轻度拥堵"
    return "整体畅通"


def _build_traffic_summary_sentence(
    traffic_label: str,
    traffic_mix_pct: float,
    _sample_count: int,
) -> str:
    """生成适合直接展示在通知里的路况摘要。"""
    if traffic_label == "高压拥堵":
        prefix = "沿途拥堵压力较高"
    elif traffic_label == "明显拥堵":
        prefix = "沿途缓行路段偏多"
    elif traffic_label == "轻度拥堵":
        prefix = "沿途有一定缓行波动"
    else:
        prefix = "沿途整体较为畅通"

    if traffic_mix_pct >= 1:
        return f"{prefix}，缓行/拥堵路段约占 {traffic_mix_pct:.0f}%"

    return prefix


def _haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """计算两点之间的大圆距离。"""
    radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def _to_float(value: object) -> float:
    """把高德百分比字段安全转换为浮点数。"""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float | str):
        try:
            return float(value)
        except ValueError:
            return 0.0

    return 0.0


def _to_optional_float(value: object) -> float | None:
    """把可选数值安全转换为浮点数。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float | str):
        try:
            return float(value)
        except ValueError:
            return None

    return None


def _to_int(value: object, default: int) -> int:
    """把任意对象安全转换为整数。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        try:
            return int(value)
        except ValueError:
            return default

    return default


def _clamp(value: float, min_value: float, max_value: float) -> float:
    """限制数值范围。"""
    return max(min_value, min(value, max_value))
