"""声明领域层依赖的仓储协议，使持久化实现可以独立替换。"""

from typing import Protocol

from evalhub.domain.entities import (
    BenchmarkRecord,
    DatasetRecord,
    EvaluationJob,
    EvaluationSampleResult,
    ModelRecord,
)


class ModelRepository(Protocol):
    """约定模型记录的写入与按标识读取能力。"""

    def add(self, model: ModelRecord) -> ModelRecord:
        """保存模型记录并返回实现最终持久化的对象。"""
        ...

    def get(self, model_id: str) -> ModelRecord:
        """按模型标识读取记录，未命中时由实现抛出 ``KeyError``。"""
        ...


class DatasetRepository(Protocol):
    """约定数据集注册信息的保存与查询能力。"""

    def add(self, dataset: DatasetRecord) -> DatasetRecord:
        """保存数据集记录并返回实现最终持久化的对象。"""
        ...

    def get(self, dataset_id: str) -> DatasetRecord:
        """按数据集标识读取记录，未命中时由实现抛出 ``KeyError``。"""
        ...


class BenchmarkRepository(Protocol):
    """约定 Benchmark 定义的保存与查询能力。"""

    def add(self, benchmark: BenchmarkRecord) -> BenchmarkRecord:
        """保存 Benchmark 记录并返回实现最终持久化的对象。"""
        ...

    def get(self, benchmark_id: str) -> BenchmarkRecord:
        """按 Benchmark 标识读取记录，未命中时由实现抛出 ``KeyError``。"""
        ...


class EvaluationJobRepository(Protocol):
    """约定评测任务的创建、读取和状态更新能力。"""

    def add(self, job: EvaluationJob) -> EvaluationJob:
        """创建评测任务记录并返回实际保存的对象。"""
        ...

    def get(self, job_id: str) -> EvaluationJob:
        """按任务标识读取记录，未命中时由实现抛出 ``KeyError``。"""
        ...

    def update(self, job: EvaluationJob) -> EvaluationJob:
        """持久化任务状态变化并返回更新后的对象。"""
        ...


class ResultRepository(Protocol):
    """约定样本级结果的批量写入与按任务查询能力。"""

    def add_many(
        self, results: list[EvaluationSampleResult]
    ) -> list[EvaluationSampleResult]:
        """批量保存样本结果并返回实际写入的结果列表。"""
        ...

    def list_by_job(self, job_id: str) -> list[EvaluationSampleResult]:
        """返回指定评测任务关联的全部样本级结果。"""
        ...
