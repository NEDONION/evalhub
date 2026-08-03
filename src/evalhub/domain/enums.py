"""定义模型来源与评测任务生命周期使用的领域枚举。"""

from enum import StrEnum


class ModelType(StrEnum):
    """标识待评测模型的训练阶段或外部接入形态。"""

    # 这些稳定字符串会进入配置、持久化记录和 API 响应，不应随展示文案变化。
    BASE = "base"
    SFT = "sft"
    RLHF = "rlhf"
    AGENT = "agent"
    API = "api"


class JobStatus(StrEnum):
    """描述评测任务从创建到终止的可观察生命周期状态。"""

    # 终态同时包含成功、失败与主动取消，便于调度器统一判断任务是否结束。
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
