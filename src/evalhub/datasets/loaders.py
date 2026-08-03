"""负责下载、校验并把 GSM8K 与 MMLU 原始文件转换为领域样本。"""

import csv
import json
import re
import tarfile
from collections.abc import Iterable
from pathlib import Path
from urllib.request import urlretrieve

from evalhub.datasets.catalog import DatasetSpec, get_dataset_spec
from evalhub.domain import EvaluationSample

# MMLU 官方测试集的完整学科列表用于 ``subject=all`` 的确定性遍历。
MMLU_SUBJECTS = [
    "abstract_algebra",
    "anatomy",
    "astronomy",
    "business_ethics",
    "clinical_knowledge",
    "college_biology",
    "college_chemistry",
    "college_computer_science",
    "college_mathematics",
    "college_medicine",
    "college_physics",
    "computer_security",
    "conceptual_physics",
    "econometrics",
    "electrical_engineering",
    "elementary_mathematics",
    "formal_logic",
    "global_facts",
    "high_school_biology",
    "high_school_chemistry",
    "high_school_computer_science",
    "high_school_european_history",
    "high_school_geography",
    "high_school_government_and_politics",
    "high_school_macroeconomics",
    "high_school_mathematics",
    "high_school_microeconomics",
    "high_school_physics",
    "high_school_psychology",
    "high_school_statistics",
    "high_school_us_history",
    "high_school_world_history",
    "human_aging",
    "human_sexuality",
    "international_law",
    "jurisprudence",
    "logical_fallacies",
    "machine_learning",
    "management",
    "marketing",
    "medical_genetics",
    "miscellaneous",
    "moral_disputes",
    "moral_scenarios",
    "nutrition",
    "philosophy",
    "prehistory",
    "professional_accounting",
    "professional_law",
    "professional_medicine",
    "professional_psychology",
    "public_relations",
    "security_studies",
    "sociology",
    "us_foreign_policy",
    "virology",
    "world_religions",
]


def prepare_dataset(name: str, *, root: Path | str = ".") -> Path:
    """下载并缓存指定公开数据集，返回可供加载的本地路径。

    Args:
        name: 受支持的数据集稳定名称。
        root: 数据缓存相对的项目根目录或替代测试目录。

    Returns:
        GSM8K 文件路径或 MMLU 测试集目录。

    Raises:
        KeyError: 数据集名称未知或尚未实现准备逻辑。
    """
    # 先解析公共规格，再把不同来源格式分派给各自的幂等准备流程。
    spec = get_dataset_spec(name)
    root_path = Path(root)
    if name == "gsm8k":
        return _prepare_gsm8k(spec, root_path)
    # MMLU 使用归档下载与安全解压流程，不能复用单文件准备逻辑。
    if name == "mmlu":
        return _prepare_mmlu(spec, root_path)
    raise KeyError(f"unsupported dataset: {name}")


def load_samples(
    name: str,
    *,
    root: Path | str = ".",
    limit: int | None = None,
    subject: str | None = None,
) -> list[EvaluationSample]:
    """从已准备的数据集中加载并限制领域评测样本。

    Args:
        name: 要加载的数据集稳定名称。
        root: 数据缓存所在的项目根目录或替代测试目录。
        limit: 最多返回的样本数；为 ``None`` 时返回全部样本。
        subject: MMLU 学科名；``None`` 或 ``all`` 表示全部官方学科。

    Returns:
        保持源文件顺序的领域样本列表。

    Raises:
        KeyError: 数据集名称尚未实现加载逻辑。
        FileNotFoundError: 数据集或指定学科尚未准备。
    """
    # 统一根路径类型后按数据集格式分派，并在生成器外层应用相同数量限制。
    root_path = Path(root)
    if name == "gsm8k":
        return _limited(_load_gsm8k(root_path), limit)
    if name == "mmlu":
        # 单学科请求只打开一个文件，全部模式则严格遵循官方学科列表顺序。
        subjects = MMLU_SUBJECTS if subject in (None, "all") else [subject]
        return _limited(_load_mmlu(root_path, subjects), limit)
    raise KeyError(f"unsupported dataset: {name}")


def _prepare_gsm8k(spec: DatasetSpec, root: Path) -> Path:
    """幂等下载 GSM8K JSONL 测试文件并返回本地路径。"""
    # 先创建父目录；已存在的文件视为可复用缓存，不重复触发网络请求。
    local_path = root / spec.local_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if not local_path.exists():
        urlretrieve(spec.source_url, local_path)
    return local_path


def _prepare_mmlu(spec: DatasetSpec, root: Path) -> Path:
    """幂等下载并安全解压 MMLU 官方归档。

    Raises:
        RuntimeError: 归档成员试图写出目标数据目录。
    """
    # 任一测试 CSV 已存在即可判定归档完成，避免每次启动重复扫描和解压。
    local_dir = root / spec.local_path
    if local_dir.exists() and any(local_dir.glob("*_test.csv")):
        return local_dir

    # 归档使用独立缓存路径，下载中断后可由调用方删除并重新准备。
    archive_path = root / "data/raw/mmlu/data.tar"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if not archive_path.exists():
        urlretrieve(spec.source_url, archive_path)

    # 解压前逐成员验证目标路径，阻止恶意归档利用 ``..`` 路径越界写入。
    with tarfile.open(archive_path) as archive:
        _safe_extract(archive, root / "data/raw/mmlu")
    return local_dir


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    """验证归档成员均位于目标目录后执行完整解压。

    Args:
        archive: 已打开的 tar 归档对象。
        destination: 允许写入的目标目录。

    Raises:
        RuntimeError: 任一成员解析后的路径逃逸目标目录。
    """
    # 先规范化目标与成员绝对路径，避免相对路径片段绕过目录边界判断。
    destination = destination.resolve()
    for member in archive.getmembers():
        member_path = (destination / member.name).resolve()
        if not str(member_path).startswith(str(destination)):
            raise RuntimeError(f"unsafe archive member path: {member.name}")
    # 只有全部成员通过检查后才写入磁盘，避免留下部分解压产物。
    archive.extractall(destination)


def _load_gsm8k(root: Path) -> Iterable[EvaluationSample]:
    """逐行读取 GSM8K JSONL 并生成数学推理评测样本。

    Raises:
        FileNotFoundError: GSM8K 测试文件尚未准备。
        ValueError: 某条记录无法提取官方最终答案。
    """
    # 路径始终取自目录规格，避免加载器与下载位置发生漂移。
    path = root / get_dataset_spec("gsm8k").local_path
    if not path.exists():
        raise FileNotFoundError("GSM8K is not prepared. Run: evalhub prepare-dataset gsm8k")

    # 以源文件行号生成稳定样本标识，并保持官方测试集顺序。
    with path.open("r", encoding="utf-8") as file:
        for index, line in enumerate(file, start=1):
            row = json.loads(line)
            reference = _extract_gsm8k_answer(row["answer"])
            # 提示词明确约束只返回数值，减少本地模型输出格式对评测器的干扰。
            prompt = (
                "Solve the following grade-school math problem. "
                "Return the final answer as a single number.\n\n"
                f"Problem: {row['question']}\n\nFinal answer:"
            )
            # 原始问题保存在元数据中，便于失败样本报告展示而无需重读源文件。
            yield EvaluationSample(
                id=f"gsm8k_test_{index}",
                input=prompt,
                reference=reference,
                metadata={"dataset": "gsm8k", "raw_question": row["question"]},
            )


def _extract_gsm8k_answer(answer: str) -> str:
    """提取 GSM8K 官方 ``####`` 标记后的标准数值答案。

    Raises:
        ValueError: 文本中不存在符合官方格式的最终数值。
    """
    # 正则兼容正负数、千位分隔符和小数，并要求官方最终答案标记存在。
    match = re.search(r"####\s*([-+]?\d[\d,]*(?:\.\d+)?)", answer)
    if not match:
        raise ValueError(f"cannot extract GSM8K final answer: {answer[:120]}")
    return match.group(1).replace(",", "")


def _load_mmlu(root: Path, subjects: list[str]) -> Iterable[EvaluationSample]:
    """按学科顺序读取 MMLU CSV 并生成选择题评测样本。

    Args:
        root: 数据缓存所在的项目根目录。
        subjects: 需要读取的 MMLU 学科名称列表。

    Raises:
        FileNotFoundError: MMLU 目录或任一学科文件尚未准备。
    """
    # 目录位置来自数据集规格，确保准备流程与加载流程引用同一缓存。
    base_dir = root / get_dataset_spec("mmlu").local_path
    if not base_dir.exists():
        raise FileNotFoundError("MMLU is not prepared. Run: evalhub prepare-dataset mmlu")

    # 逐学科打开文件，错误会包含精确路径，便于定位不完整的归档缓存。
    for subject in subjects:
        path = base_dir / f"{subject}_test.csv"
        if not path.exists():
            raise FileNotFoundError(f"MMLU subject file not found: {path}")
        with path.open("r", encoding="utf-8") as file:
            # 官方文件为无表头 CSV，直接按固定六列结构逐行解析。
            reader = csv.reader(file)
            for index, row in enumerate(reader, start=1):
                # 非完整行无法提供四个选项和答案，按坏数据跳过而不生成错误样本。
                if len(row) < 6:
                    continue
                question, option_a, option_b, option_c, option_d, answer = row[:6]
                # 统一提示格式并限制返回单个字母，提高不同本地模型输出的可比性。
                prompt = (
                    "Answer the multiple-choice question. "
                    "Return only one letter: A, B, C, or D.\n\n"
                    f"Subject: {subject.replace('_', ' ')}\n"
                    f"Question: {question}\n"
                    f"A. {option_a}\n"
                    f"B. {option_b}\n"
                    f"C. {option_c}\n"
                    f"D. {option_d}\n\n"
                    "Answer:"
                )
                # 保存学科和原题元数据，使报告能够在不依赖 CSV 的情况下解释失败样本。
                yield EvaluationSample(
                    id=f"mmlu_{subject}_{index}",
                    input=prompt,
                    reference=answer.strip().upper(),
                    metadata={"dataset": "mmlu", "subject": subject, "question": question},
                )


def _limited(samples: Iterable[EvaluationSample], limit: int | None) -> list[EvaluationSample]:
    """按输入顺序收集生成器，并在达到可选上限时提前停止。

    Args:
        samples: 可惰性产生领域样本的迭代器。
        limit: 最大样本数；为 ``None`` 时消费全部样本。

    Returns:
        最多包含指定数量样本的新列表。
    """
    # 边迭代边检查上限，避免快速试跑仍解析完整大型数据集。
    output: list[EvaluationSample] = []
    for sample in samples:
        output.append(sample)
        # 达到限制后立即停止上游生成器，避免继续执行文件解析与对象创建。
        if limit is not None and len(output) >= limit:
            break
    return output
