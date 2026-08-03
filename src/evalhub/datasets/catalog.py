from dataclasses import dataclass


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    display_name: str
    task_type: str
    evaluator_type: str
    homepage: str
    source_url: str
    local_path: str
    description: str


def dataset_catalog() -> dict[str, DatasetSpec]:
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
    catalog = dataset_catalog()
    try:
        return catalog[name]
    except KeyError as exc:
        available = ", ".join(sorted(catalog))
        raise KeyError(f"unknown dataset: {name}; available: {available}") from exc
