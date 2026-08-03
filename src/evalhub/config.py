"""从环境变量构建 EvalHub 基础设施连接配置。"""

from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True)
class Settings:
    """集中保存运行环境、数据库、消息代理和对象存储设置。"""

    # 本地模式与存储桶提供安全默认值，外部基础设施连接保持可选。
    env: str = "local"
    database_url: str | None = None
    broker_url: str | None = None
    storage_endpoint: str | None = None
    storage_bucket: str = "evalhub"


def load_settings() -> Settings:
    """读取当前进程环境变量并返回不可变配置快照。"""
    # 每次显式读取环境，便于测试通过临时变量构造相互隔离的配置实例。
    return Settings(
        env=getenv("EVALHUB_ENV", "local"),
        database_url=getenv("EVALHUB_DATABASE_URL"),
        broker_url=getenv("EVALHUB_BROKER_URL"),
        storage_endpoint=getenv("EVALHUB_STORAGE_ENDPOINT"),
        storage_bucket=getenv("EVALHUB_STORAGE_BUCKET", "evalhub"),
    )
