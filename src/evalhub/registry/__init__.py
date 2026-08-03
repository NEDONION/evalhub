"""公开适用于本地运行和测试的内存 Registry 实现。"""

from evalhub.registry.in_memory import InMemoryRegistry

# 只暴露组合后的 Registry，内部表结构仍可在不影响调用方的情况下演进。
__all__ = ["InMemoryRegistry"]
