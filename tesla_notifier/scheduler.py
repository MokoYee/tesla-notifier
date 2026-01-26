"""定时任务模块"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from tesla_notifier.config import config
from tesla_notifier.logger import setup_logger

logger = setup_logger("scheduler")


def parse_cron(cron_expr: str) -> CronTrigger:
    """解析 cron 表达式"""
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError(f"无效的 cron 表达式: {cron_expr}")

    return CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
        timezone=config.timezone,
    )


class Scheduler:
    """定时任务调度器"""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone=config.timezone)
        self._daily_task: object = None
        self._weekly_task: object = None
        self._monthly_task: object = None

    def add_daily_task(self, func: object) -> None:
        """添加每日任务"""
        trigger = parse_cron(config.daily_cron)
        self._daily_task = self._scheduler.add_job(
            func,
            trigger,
            id="daily_briefing",
            misfire_grace_time=None,
        )
        logger.info(f"每日简报任务已配置: {config.daily_cron}")

    def add_weekly_task(self, func: object) -> None:
        """添加周报任务"""
        trigger = parse_cron(config.weekly_cron)
        self._weekly_task = self._scheduler.add_job(
            func,
            trigger,
            id="weekly_report",
            misfire_grace_time=None,
        )
        logger.info(f"周报任务已配置: {config.weekly_cron}")

    def add_monthly_task(self, func: object) -> None:
        """添加月报任务"""
        trigger = parse_cron(config.monthly_cron)
        self._monthly_task = self._scheduler.add_job(
            func,
            trigger,
            id="monthly_report",
            misfire_grace_time=None,
        )
        logger.info(f"月报任务已配置: {config.monthly_cron}")

    def start(self) -> None:
        """启动调度器"""
        self._scheduler.start()
        logger.info("定时任务调度器已启动")

    def stop(self) -> None:
        """停止调度器"""
        if self._scheduler.running:
            logger.info("正在停止定时任务调度器...")
            self._scheduler.shutdown(wait=False)  # 不等待任务完成，立即停止
            logger.info("定时任务调度器已停止")
        else:
            logger.info("定时任务调度器未运行，无需停止")

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._scheduler.running
