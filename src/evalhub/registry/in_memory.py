"""提供无外部依赖的内存 Registry，供 MVP、本地运行和单元测试使用。"""

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
    """按领域实体的字符串标识保存和读取通用记录。"""

    def __init__(self) -> None:
        """初始化保持插入顺序的空记录映射。"""
        # Python 字典保留插入顺序，因此列表查询能稳定复现注册先后关系。
        self._items: dict[str, T] = {}

    def add(self, item: T) -> T:
        """按实体自身标识写入或覆盖记录并返回原对象。

        Args:
            item: 遵循运行时 ``id`` 属性约定的领域实体。

        Returns:
            实际写入表中的同一实体对象。
        """
        # 泛型表接收多种领域实体，只能在运行时通过共同的 ``id`` 约定取得主键。
        item_id = getattr(item, "id")  # noqa: B009
        self._items[item_id] = item
        return item

    def get(self, item_id: str) -> T:
        """按标识读取记录并把底层缺失错误转换为可诊断信息。

        Raises:
            KeyError: 指定标识未在当前内存表中注册。
        """
        try:
            return self._items[item_id]
        except KeyError as exc:
            # 保留原始异常因果链，同时提供包含具体标识的领域化错误消息。
            raise KeyError(f"record not found: {item_id}") from exc

    def list(self) -> list[T]:
        """按记录插入顺序返回与内部存储隔离的新列表。"""
        # 返回列表副本，避免调用方通过容器操作直接改变表的内部映射。
        return list(self._items.values())


class _JobTable(_Table[EvaluationJob]):
    """扩展通用表以支持可变评测任务的状态更新。"""

    def update(self, job: EvaluationJob) -> EvaluationJob:
        """用相同任务标识下的最新状态覆盖现有对象。"""
        # 任务实体本身可变，按稳定标识覆盖即可保留 Registry 的查询语义。
        self._items[job.id] = job
        return job


class _ResultTable:
    """按写入顺序保存样本结果并支持任务维度过滤。"""

    def __init__(self) -> None:
        """初始化空的样本结果序列。"""
        # 结果使用列表而非主键映射，便于保留批次内和批次间的原始顺序。
        self._items: list[EvaluationSampleResult] = []

    def add_many(self, results: list[EvaluationSampleResult]) -> list[EvaluationSampleResult]:
        """批量追加样本结果并返回调用方传入的列表。"""
        # 一次扩展保持输入顺序，返回原列表便于上层继续构建同批次报告。
        self._items.extend(results)
        return results

    def list_by_job(self, job_id: str) -> list[EvaluationSampleResult]:
        """筛选并返回属于指定评测任务的全部样本结果。"""
        # 创建新列表隔离内部容器，同时保留原始结果写入顺序。
        return [result for result in self._items if result.job_id == job_id]


class InMemoryRegistry:
    """聚合模型、数据集、Benchmark、任务和结果的内存仓储。"""

    def __init__(self) -> None:
        """为每类领域记录创建职责单一的内存表。"""
        # 各表独立维护存储结构，组合对象只负责提供统一的依赖装配入口。
        self.models = _Table[ModelRecord]()
        self.datasets = _Table[DatasetRecord]()
        self.benchmarks = _Table[BenchmarkRecord]()
        self.jobs = _JobTable()
        self.results = _ResultTable()
