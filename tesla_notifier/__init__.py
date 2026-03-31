"""Tesla Notifier - TeslaMate 推送通知服务。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tesla-notifier")
except PackageNotFoundError:
    __version__ = "1.2.0"

__all__ = ["__version__"]
