from enum import StrEnum


class ModelType(StrEnum):
    BASE = "base"
    SFT = "sft"
    RLHF = "rlhf"
    AGENT = "agent"
    API = "api"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
