"""负责下载、校验并把公开数据集与 Hexagon 固定来源转换为领域样本。"""

import csv
import hashlib
import json
import shutil
import tarfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.request import urlretrieve

from evalhub.datasets.catalog import DatasetSpec, get_dataset_spec
from evalhub.datasets.hexagon_manifest import HexagonSampleSpec, hexagon_manifest
from evalhub.datasets.hexagon_sources import (
    NormalizedSourceRow,
    extract_gsm8k_answer,
    gsm8k_prompt,
    hexagon_source_specs,
    load_hexagon_source_rows,
    mmlu_prompt,
    prepare_hexagon_dataset,
)
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


def prepare_dataset(
    name: str, *, root: Path | str = ".", force: bool = False
) -> Path:
    """下载并缓存指定公开数据集，返回可供加载的本地路径。

    Args:
        name: 受支持的数据集稳定名称。
        root: 数据缓存相对的项目根目录或替代测试目录。
        force: 是否重新下载并在校验后替换已有缓存。

    Returns:
        GSM8K 文件路径或 MMLU 测试集目录。

    Raises:
        KeyError: 数据集名称未知或尚未实现准备逻辑。
    """
    # 先解析公共规格，再把不同来源格式分派给各自的幂等准备流程。
    spec = get_dataset_spec(name)
    root_path = Path(root)
    if name.startswith("hexagon-"):
        return prepare_hexagon_dataset(name, root=root_path, force=force)
    if name == "gsm8k":
        return _prepare_gsm8k(spec, root_path, force=force)
    # MMLU 使用归档下载与安全解压流程，不能复用单文件准备逻辑。
    if name == "mmlu":
        return _prepare_mmlu(spec, root_path, force=force)
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
    if name.startswith("hexagon-"):
        return load_hexagon_samples(name, root=root_path, limit=limit)
    if name == "gsm8k":
        return _limited(_load_gsm8k(root_path), limit)
    if name == "mmlu":
        # 单学科请求只打开一个文件，全部模式则严格遵循官方学科列表顺序。
        subjects = MMLU_SUBJECTS if subject in (None, "all") else [subject]
        return _limited(_load_mmlu(root_path, subjects), limit)
    raise KeyError(f"unsupported dataset: {name}")


def load_hexagon_samples(
    name: str,
    root: Path | str = ".",
    limit: int | None = None,
    *,
    manifest: tuple[HexagonSampleSpec, ...] | None = None,
) -> list[EvaluationSample]:
    """按冻结清单顺序加载一个 Hexagon 来源切片的英文领域样本。

    Args:
        name: 七个固定 Hexagon Benchmark ID 之一。
        root: 已准备原始资产相对的项目根目录或测试临时目录。
        limit: 在完整清单顺序建立后最多返回的样本数量。
        manifest: 测试可注入的已解析清单；注入时直接解析局部夹具，缺省生产路径读取包内清单
            并复核 Task 2 固定文件摘要。

    Returns:
        仅英文进入 ``input`` 和 ``reference``、中文只在元数据中的样本列表。

    Raises:
        KeyError: Benchmark ID 未注册时抛出。
        FileNotFoundError: 固定来源资产尚未准备时抛出。
        ValueError: 固定文件、清单选择器或英文内容的摘要发生漂移，或选择器重复时抛出。
    """
    source = hexagon_source_specs()[name]
    root_path = Path(root)
    source_path = root_path / source.cache_path
    if not source_path.exists():
        raise FileNotFoundError(f"Hexagon source is not prepared: {source_path}")
    # 生产加载复用 Task 2 的整文件摘要边界；注入清单仅用于离线局部夹具测试。
    if manifest is None:
        source_path = prepare_hexagon_dataset(name, root=root_path)
        frozen = hexagon_manifest()
    else:
        frozen = manifest
    # TruthfulQA 的输入摘要取决于清单冻结的二选一排列，解析前先建立来源键映射。
    option_orders = {
        item.source_key: item.option_order
        for item in frozen
        if item.benchmark_id == "hexagon-truthfulqa" and item.option_order is not None
    }
    rows = load_hexagon_source_rows(name, source_path, option_orders=option_orders)
    return _limited(_selected_samples(name, rows, frozen), limit)


def _selected_samples(
    benchmark_id: str,
    rows: Mapping[str, NormalizedSourceRow],
    manifest: tuple[HexagonSampleSpec, ...],
) -> list[EvaluationSample]:
    """按冻结清单顺序组装英文样本，并拒绝重复或缺失的来源选择器。

    Args:
        benchmark_id: 当前加载的 Hexagon 来源切片 ID。
        rows: 已由对应固定来源解析器生成的来源键映射。
        manifest: 包含当前来源选择器和双语展示元数据的冻结清单。

    Returns:
        顺序与清单一致、内容摘要已经复核的领域评测样本列表。

    Raises:
        ValueError: 当前来源清单键重复、缺失或英文摘要不匹配时抛出。
    """
    selected = [item for item in manifest if item.benchmark_id == benchmark_id]
    keys = [item.source_key for item in selected]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate source selectors for {benchmark_id}")
    output: list[EvaluationSample] = []
    for item in selected:
        row = rows.get(item.source_key)
        if row is None:
            raise ValueError(f"missing source selector: {item.source_key}")
        output.append(_to_evaluation_sample(item, row))
    return output


def _to_evaluation_sample(
    spec: HexagonSampleSpec, row: NormalizedSourceRow
) -> EvaluationSample:
    """复核英文正文摘要并把中文辅助内容限制在领域样本元数据中。

    Args:
        spec: 冻结清单中与官方来源键匹配的样本规格。
        row: 从已校验固定资产解析得到的英文规范化记录。

    Returns:
        可送入模型和评分器的英文样本；中文字段只存在于 ``metadata``。

    Raises:
        ValueError: 规范化英文输入或参考答案与清单摘要不一致时抛出。
    """
    input_digest = hashlib.sha256(row.input.encode("utf-8")).hexdigest()
    if input_digest != spec.input_sha256:
        raise ValueError(f"input SHA-256 mismatch for {spec.source_key}")
    reference_digest = hashlib.sha256(row.reference.encode("utf-8")).hexdigest()
    if reference_digest != spec.reference_sha256:
        raise ValueError(f"reference SHA-256 mismatch for {spec.source_key}")
    # 来源元数据先复制，随后由清单字段覆盖同名键，保证展示溯源不可被上游对象伪造。
    metadata = dict(row.source_metadata)
    metadata.update(
        {
            "dataset": spec.benchmark_id,
            "source_key": spec.source_key,
            "selection_stratum": spec.selection_stratum,
            "evaluator_type": row.evaluator_type,
            "input_zh": spec.input_zh,
            "reference_zh": spec.reference_zh,
            "translation_version": spec.translation_version,
        }
    )
    return EvaluationSample(id=spec.id, input=row.input, reference=row.reference, metadata=metadata)


def _prepare_gsm8k(spec: DatasetSpec, root: Path, *, force: bool) -> Path:
    """幂等下载 GSM8K JSONL 测试文件并返回本地路径。

    Args:
        spec: 包含官方来源与仓库相对缓存路径的数据集规格。
        root: 数据缓存相对的项目根目录或测试临时目录。
        force: 是否下载候选文件并在完整校验后替换已有缓存。

    Returns:
        可供样本加载器直接读取的 GSM8K JSONL 路径。

    Raises:
        ValueError: 强制更新下载的候选文件为空或记录格式无效。
        OSError: 下载、临时文件或原子替换操作失败。
    """
    # 先创建父目录；已存在的文件视为可复用缓存，不重复触发网络请求。
    local_path = root / spec.local_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if local_path.exists() and not force:
        return local_path
    if not force:
        urlretrieve(spec.source_url, local_path)
        return local_path

    # 强制更新不直接覆盖可用缓存；候选文件只有通过逐行校验后才执行同目录原子替换。
    with NamedTemporaryFile(
        prefix=".evalhub-", suffix=".jsonl", dir=local_path.parent, delete=False
    ) as temporary:
        candidate = Path(temporary.name)
    try:
        urlretrieve(spec.source_url, candidate)
        _validate_gsm8k_cache(candidate)
        candidate.replace(local_path)
    finally:
        candidate.unlink(missing_ok=True)
    return local_path


def _prepare_mmlu(spec: DatasetSpec, root: Path, *, force: bool) -> Path:
    """幂等下载并安全解压 MMLU 官方归档。

    Args:
        spec: 包含官方归档来源与测试集相对目录的数据集规格。
        root: 数据缓存相对的项目根目录或测试临时目录。
        force: 是否下载候选归档并在校验后替换现有目录。

    Returns:
        包含 MMLU `*_test.csv` 文件的本地目录。

    Raises:
        RuntimeError: 归档成员试图写出目标数据目录。
        ValueError: 强制更新的候选归档缺少有效测试 CSV。
    """
    # 任一测试 CSV 已存在即可判定归档完成，避免每次启动重复扫描和解压。
    local_dir = root / spec.local_path
    if local_dir.exists() and any(local_dir.glob("*_test.csv")) and not force:
        return local_dir

    # 归档使用独立缓存路径，下载中断后可由调用方删除并重新准备。
    archive_path = root / "data/raw/mmlu/data.tar"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if force:
        return _refresh_mmlu(spec, archive_path.parent, local_dir)
    if not archive_path.exists():
        urlretrieve(spec.source_url, archive_path)

    # 解压前逐成员验证目标路径，阻止恶意归档利用 ``..`` 路径越界写入。
    with tarfile.open(archive_path) as archive:
        _safe_extract(archive, root / "data/raw/mmlu")
    return local_dir


def _validate_gsm8k_cache(path: Path) -> None:
    """完整解析候选 GSM8K 文件，拒绝空文件和缺少官方答案的记录。

    Args:
        path: 尚未替换正式缓存的候选 JSONL 文件。

    Raises:
        ValueError: 文件为空、JSON 无效、问题为空或官方最终答案不可提取。
    """
    count = 0
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                # 每条记录同时校验问题与官方答案，避免损坏缓存延迟到评测阶段才暴露。
                row = json.loads(line)
                question = row.get("question")
                answer = row.get("answer")
                if not isinstance(question, str) or not question.strip():
                    raise ValueError("GSM8K row is missing a question")
                if not isinstance(answer, str):
                    raise ValueError("GSM8K row is missing an answer")
                extract_gsm8k_answer(answer)
                count += 1
    except (json.JSONDecodeError, OSError, TypeError) as exc:
        raise ValueError(f"invalid GSM8K cache: {exc}") from exc
    if count == 0:
        raise ValueError("invalid GSM8K cache: no samples")


def _refresh_mmlu(spec: DatasetSpec, mmlu_root: Path, local_dir: Path) -> Path:
    """验证候选 MMLU 归档并在同一文件系统中交换目录与归档。

    Args:
        spec: 提供 MMLU 官方归档 URL 的数据集规格。
        mmlu_root: 正式 `data` 目录和 `data.tar` 所在的缓存根目录。
        local_dir: 成功替换后返回给调用方的测试集目录。

    Returns:
        已完成校验和交换的 MMLU 测试集目录。

    Raises:
        ValueError: 候选归档不包含非空测试 CSV。
        RuntimeError: 归档成员路径逃逸候选解压目录。
        OSError: 下载、解压或目录交换失败；已有缓存会尽力回滚。
    """
    archive_path = mmlu_root / "data.tar"
    target_data = mmlu_root / "data"
    with TemporaryDirectory(prefix=".evalhub-", dir=mmlu_root) as temporary:
        workspace = Path(temporary)
        candidate_archive = workspace / "data.tar"
        extract_root = workspace / "extracted"
        extract_root.mkdir()
        # 候选归档和解压目录均位于正式缓存同一文件系统，保证最后的 replace 可原子完成。
        urlretrieve(spec.source_url, candidate_archive)
        with tarfile.open(candidate_archive) as archive:
            _safe_extract(archive, extract_root)

        candidate_data = extract_root / "data"
        candidate_test = candidate_data / "test"
        csv_files = list(candidate_test.glob("*_test.csv"))
        if not csv_files or not all(path.stat().st_size > 0 for path in csv_files):
            raise ValueError("invalid MMLU cache: test CSV files are missing or empty")

        # 旧目录和归档先移入临时工作区，任一步骤失败都能按相反顺序恢复原缓存。
        backup_data = workspace / "old-data"
        backup_archive = workspace / "old-data.tar"
        data_installed = False
        archive_installed = False
        try:
            if target_data.exists():
                target_data.replace(backup_data)
            if archive_path.exists():
                archive_path.replace(backup_archive)
            # 两个候选资产都安装成功后，临时目录退出会自动清理旧缓存备份。
            candidate_data.replace(target_data)
            data_installed = True
            candidate_archive.replace(archive_path)
            archive_installed = True
        except Exception:
            # 文件系统边界可能从多个具体 OSError 子类失败，统一回滚后保留原异常因果。
            if data_installed and target_data.exists():
                shutil.rmtree(target_data)
            if archive_installed:
                archive_path.unlink(missing_ok=True)
            if backup_data.exists():
                backup_data.replace(target_data)
            if backup_archive.exists():
                backup_archive.replace(archive_path)
            raise
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
        if not member_path.is_relative_to(destination):
            raise RuntimeError(f"unsafe archive member path: {member.name}")
    # 只有全部成员通过检查后才写入磁盘；新版本显式指定过滤策略以消除默认值迁移告警。
    try:
        archive.extractall(destination, filter="fully_trusted")
    except TypeError:
        # Python 3.11 尚不支持 ``filter``，前面的逐成员路径校验仍提供相同越界保护。
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
            reference = extract_gsm8k_answer(row["answer"])
            # 提示格式与 Hexagon 共享同一函数，防止两条入口产生不同的模型输入。
            prompt = gsm8k_prompt(row["question"])
            # 原始问题保存在元数据中，便于失败样本报告展示而无需重读源文件。
            yield EvaluationSample(
                id=f"gsm8k_test_{index}",
                input=prompt,
                reference=reference,
                metadata={"dataset": "gsm8k", "raw_question": row["question"]},
            )


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
                question = row[0]
                answer = row[5]
                # 提示格式与 Hexagon 共享同一函数，保持旧 MMLU 行为不变。
                prompt = mmlu_prompt(subject, row[:6])
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
