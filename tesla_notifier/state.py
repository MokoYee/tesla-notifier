"""状态持久化模块

使用 JSON 文件存储已推送的行程和充电记录 ID，
防止服务重启后重复推送。
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from tesla_notifier.logger import setup_logger

logger = setup_logger("state")

# 默认状态文件路径
DEFAULT_STATE_FILE = "./data/state.json"


@dataclass
class PushState:
    """推送状态管理器

    负责持久化已推送的行程和充电记录 ID，
    防止服务重启后重复推送。
    """

    state_file: Path = field(
        default_factory=lambda: Path(os.getenv("STATE_FILE", DEFAULT_STATE_FILE))
    )
    pushed_trips: set[int] = field(default_factory=set)
    pushed_charges: set[int] = field(default_factory=set)
    _max_entries: int = 1000  # 每个集合最大条目数，防止无限增长

    def __post_init__(self) -> None:
        """初始化时加载状态"""
        self._load()

    def _load(self) -> None:
        """从文件加载状态"""
        if not self.state_file.exists():
            logger.info(f"状态文件不存在，使用空状态: {self.state_file}")
            return

        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.pushed_trips = set(data.get("pushed_trips", []))
            self.pushed_charges = set(data.get("pushed_charges", []))
            logger.info(
                f"已加载状态: {len(self.pushed_trips)} 行程, "
                f"{len(self.pushed_charges)} 充电记录"
            )
        except Exception as e:
            logger.warning(f"加载状态文件失败，使用空状态: {e}")

    def _save(self) -> None:
        """保存状态到文件"""
        try:
            # 确保目录存在
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "pushed_trips": list(self.pushed_trips),
                "pushed_charges": list(self.pushed_charges),
            }
            self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"保存状态文件失败: {e}")

    def _trim_if_needed(self, s: set[int]) -> None:
        """如果集合过大，删除最旧的条目（ID 较小的）"""
        if len(s) > self._max_entries:
            # 保留最新的一半（ID 较大的）
            sorted_ids = sorted(s)
            to_remove = sorted_ids[: len(s) - self._max_entries // 2]
            for id_ in to_remove:
                s.discard(id_)

    def is_trip_pushed(self, trip_id: int) -> bool:
        """检查行程是否已推送"""
        return trip_id in self.pushed_trips

    def mark_trip_pushed(self, trip_id: int) -> None:
        """标记行程已推送"""
        self.pushed_trips.add(trip_id)
        self._trim_if_needed(self.pushed_trips)
        self._save()

    def is_charge_pushed(self, charge_id: int) -> bool:
        """检查充电记录是否已推送"""
        return charge_id in self.pushed_charges

    def mark_charge_pushed(self, charge_id: int) -> None:
        """标记充电记录已推送"""
        self.pushed_charges.add(charge_id)
        self._trim_if_needed(self.pushed_charges)
        self._save()


# 全局单例
push_state = PushState()
