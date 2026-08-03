from typing import Generic, TypeVar

from evalhub.domain.entities import (
    BenchmarkRecord,
    DatasetRecord,
    EvaluationJob,
    EvaluationSampleResult,
    ModelRecord,
)

T = TypeVar("T")


class _Table(Generic[T]):
    def __init__(self) -> None:
        self._items: dict[str, T] = {}

    def add(self, item: T) -> T:
        item_id = getattr(item, "id")
        self._items[item_id] = item
        return item

    def get(self, item_id: str) -> T:
        try:
            return self._items[item_id]
        except KeyError as exc:
            raise KeyError(f"record not found: {item_id}") from exc

    def list(self) -> list[T]:
        return list(self._items.values())


class _JobTable(_Table[EvaluationJob]):
    def update(self, job: EvaluationJob) -> EvaluationJob:
        self._items[job.id] = job
        return job


class _ResultTable:
    def __init__(self) -> None:
        self._items: list[EvaluationSampleResult] = []

    def add_many(self, results: list[EvaluationSampleResult]) -> list[EvaluationSampleResult]:
        self._items.extend(results)
        return results

    def list_by_job(self, job_id: str) -> list[EvaluationSampleResult]:
        return [result for result in self._items if result.job_id == job_id]


class InMemoryRegistry:
    def __init__(self) -> None:
        self.models = _Table[ModelRecord]()
        self.datasets = _Table[DatasetRecord]()
        self.benchmarks = _Table[BenchmarkRecord]()
        self.jobs = _JobTable()
        self.results = _ResultTable()
