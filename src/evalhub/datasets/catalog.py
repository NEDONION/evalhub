"""维护 EvalHub 内置公开数据集的稳定元数据目录。"""

from dataclasses import dataclass

from evalhub.datasets.hexagon_sources import hexagon_source_specs


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
    catalog = {
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
    # Hexagon 原始资产由固定来源目录统一定义，目录条目只补充加载和展示所需的信息。
    attributes = {
        "hexagon-mmlu": (
            "multiple_choice",
            "choice_letter",
            "https://github.com/hendrycks/test",
        ),
        "hexagon-ifeval": (
            "instruction_following",
            "ifeval_strict",
            "https://github.com/google-research/google-research/tree/master/instruction_following_eval",
        ),
        "hexagon-gsm8k": (
            "math_reasoning",
            "numeric_exact_match",
            "https://github.com/openai/grade-school-math",
        ),
        "hexagon-bbh": (
            "reasoning",
            "bbh_answer",
            "https://github.com/suzgunmirac/BIG-Bench-Hard",
        ),
        "hexagon-humaneval": ("code_generation", "pass@1", "https://github.com/openai/human-eval"),
        "hexagon-truthfulqa": (
            "multiple_choice",
            "choice_letter",
            "https://github.com/sylinrl/TruthfulQA",
        ),
        "hexagon-bbq": (
            "multiple_choice",
            "choice_letter",
            "https://github.com/nyu-mll/BBQ",
        ),
    }
    # 每个固定来源都生成对应入口，缓存位置和下载 URL 不在目录中重复维护。
    for benchmark_id, source in hexagon_source_specs().items():
        task_type, evaluator_type, homepage = attributes[benchmark_id]
        catalog[benchmark_id] = DatasetSpec(
            name=benchmark_id,
            display_name=source.source_name,
            task_type=task_type,
            evaluator_type=evaluator_type,
            homepage=homepage,
            source_url=source.url,
            local_path=source.cache_path,
            description=f"{source.source_name} 官方固定版本 Hexagon Benchmark 原始资产。",
        )
    return catalog


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
