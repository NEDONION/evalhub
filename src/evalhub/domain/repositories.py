from typing import Protocol

from evalhub.domain.entities import (
    BenchmarkRecord,
    DatasetRecord,
    EvaluationJob,
    EvaluationSampleResult,
    ModelRecord,
)


class ModelRepository(Protocol):
    def add(self, model: ModelRecord) -> ModelRecord: ...

    def get(self, model_id: str) -> ModelRecord: ...


class DatasetRepository(Protocol):
    def add(self, dataset: DatasetRecord) -> DatasetRecord: ...

    def get(self, dataset_id: str) -> DatasetRecord: ...


class BenchmarkRepository(Protocol):
    def add(self, benchmark: BenchmarkRecord) -> BenchmarkRecord: ...

    def get(self, benchmark_id: str) -> BenchmarkRecord: ...


class EvaluationJobRepository(Protocol):
    def add(self, job: EvaluationJob) -> EvaluationJob: ...

    def get(self, job_id: str) -> EvaluationJob: ...

    def update(self, job: EvaluationJob) -> EvaluationJob: ...


class ResultRepository(Protocol):
    def add_many(self, results: list[EvaluationSampleResult]) -> list[EvaluationSampleResult]: ...

    def list_by_job(self, job_id: str) -> list[EvaluationSampleResult]: ...
