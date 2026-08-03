"""维护 EvalHub 内置公开数据集的稳定元数据目录。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DatasetSpec:
    """描述公开数据集的任务类型、来源、缓存位置和展示信息。"""

    # 标识与评测器类型参与程序分派，展示字段和链接用于 CLI 与控制台说明。
    name: str
    display_name: str
    task_type: str
    evaluator_type: str
    homepage: str
    source_url: str
    local_path: str
    description: str


def dataset_catalog() -> dict[str, DatasetSpec]:
    """构建当前版本支持的数据集规格映射。

    Returns:
        以稳定数据集名称为键的新映射，调用方可安全扩展而不污染全局状态。
    """
    # 每次返回新字典和不可变规格，避免调用方修改影响其他请求或测试。
    return {
        "gsm8k": DatasetSpec(
            name="gsm8k",
            display_name="GSM8K 测试集",
            task_type="math_reasoning",
            evaluator_type="numeric_exact_match",
            homepage="https://github.com/openai/grade-school-math",
            source_url=(
                "https://raw.githubusercontent.com/openai/grade-school-math/master/"
                "grade_school_math/data/test.jsonl"
            ),
            local_path="data/raw/gsm8k/test.jsonl",
            description="OpenAI 官方小学数学推理 Benchmark 测试集。",
        ),
        "mmlu": DatasetSpec(
            name="mmlu",
            display_name="MMLU 测试集",
            task_type="multiple_choice",
            evaluator_type="choice_letter",
            homepage="https://github.com/hendrycks/test",
            source_url="https://people.eecs.berkeley.edu/~hendrycks/data.tar",
            local_path="data/raw/mmlu/data/test",
            description="Massive Multitask Language Understanding 官方多学科测试集。",
        ),
    }


def get_dataset_spec(name: str) -> DatasetSpec:
    """按稳定名称读取数据集规格并提供可用名称提示。

    Args:
        name: ``dataset_catalog`` 中注册的数据集名称。

    Returns:
        与名称对应的不可变数据集规格。

    Raises:
        KeyError: 名称未注册，并在错误中列出当前可用数据集。
    """
    # 通过统一目录查询，保证 CLI、服务端和加载器共享完全相同的注册集合。
    catalog = dataset_catalog()
    try:
        return catalog[name]
    except KeyError as exc:
        # 排序后的候选列表使错误信息稳定，便于用户直接修正配置。
        available = ", ".join(sorted(catalog))
        raise KeyError(f"unknown dataset: {name}; available: {available}") from exc
