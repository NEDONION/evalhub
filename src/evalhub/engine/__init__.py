"""公开评测执行器与报告聚合函数组成的引擎层接口。"""

from evalhub.engine.reports import build_report
from evalhub.engine.runner import EvaluationRunner, ProgressCallback, SampleResultCallback

# 限定调用方依赖的稳定入口，隐藏引擎内部模块组织方式。
__all__ = ["EvaluationRunner", "ProgressCallback", "SampleResultCallback", "build_report"]
