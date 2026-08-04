"""下载、校验并规范化 Hexagon Benchmark 的固定版本官方原始资产。"""

import csv
import gzip
import hashlib
import io
import json
import re
import tarfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlretrieve


@dataclass(frozen=True)
class PinnedSource:
    """描述一个可复现 Hexagon 原始资产的固定来源与本地缓存位置。"""

    benchmark_id: str
    source_name: str
    revision: str
    url: str
    sha256: str
    license: str
    cache_path: str


@dataclass(frozen=True)
class NormalizedSourceRow:
    """保存一条可由清单选择的英文规范化记录及官方评分元数据。"""

    source_key: str
    input: str
    reference: str
    evaluator_type: str
    source_metadata: dict[str, object]


@dataclass(frozen=True)
class HumanEvalSourceRow:
    """保存仅供 Docker 评分边界使用的选中 HumanEval 官方执行字段。"""

    source_key: str
    prompt: str
    canonical_solution: str
    test: str
    entry_point: str


PINNED_SOURCES = (
    PinnedSource(
        "hexagon-mmlu",
        "MMLU",
        "sha256:bec563ba4bac1d6aaf04141cd7d1605d7a5ca833e38f994051e818489592989b",
        "https://people.eecs.berkeley.edu/~hendrycks/data.tar",
        "bec563ba4bac1d6aaf04141cd7d1605d7a5ca833e38f994051e818489592989b",
        "MIT",
        "data/raw/mmlu/data.tar",
    ),
    PinnedSource(
        "hexagon-ifeval",
        "IFEval",
        "8dadc6c56e2c2e51a9dd7e0d4bf2840922b4b6c0",
        (
            "https://raw.githubusercontent.com/google-research/google-research/"
            "8dadc6c56e2c2e51a9dd7e0d4bf2840922b4b6c0/"
            "instruction_following_eval/data/input_data.jsonl"
        ),
        "67ffeee0fcb87c317c5b08a2de85557b4a7e96ada6178aa645b4954fe4b53d49",
        "CC-BY-4.0",
        "data/raw/hexagon/ifeval/input_data.jsonl",
    ),
    PinnedSource(
        "hexagon-gsm8k",
        "GSM8K",
        "3101c7d5072418e28b9008a6636bde82a006892c",
        (
            "https://raw.githubusercontent.com/openai/grade-school-math/"
            "3101c7d5072418e28b9008a6636bde82a006892c/"
            "grade_school_math/data/test.jsonl"
        ),
        "3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14",
        "MIT",
        "data/raw/gsm8k/test.jsonl",
    ),
    PinnedSource(
        "hexagon-bbh",
        "BIG-Bench Hard",
        "9ee07bd481feebf959a6b59d61ea57bdcf30964d",
        "https://github.com/suzgunmirac/BIG-Bench-Hard/archive/9ee07bd481feebf959a6b59d61ea57bdcf30964d.tar.gz",
        "0bb15e11935747f7cfa42ef2e02254b70f9c9e545f6dabfd374dec3b6ba95bbc",
        "MIT",
        "data/raw/hexagon/bbh/archive.tar.gz",
    ),
    PinnedSource(
        "hexagon-humaneval",
        "HumanEval",
        "6d43fb980f9fee3c892a914eda09951f772ad10d",
        (
            "https://raw.githubusercontent.com/openai/human-eval/"
            "6d43fb980f9fee3c892a914eda09951f772ad10d/data/HumanEval.jsonl.gz"
        ),
        "b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef",
        "MIT",
        "data/raw/hexagon/humaneval/HumanEval.jsonl.gz",
    ),
    PinnedSource(
        "hexagon-truthfulqa",
        "TruthfulQA",
        "d71c110897f5d31c5d7f309e7bc316c152f6f031",
        (
            "https://raw.githubusercontent.com/sylinrl/TruthfulQA/"
            "d71c110897f5d31c5d7f309e7bc316c152f6f031/TruthfulQA.csv"
        ),
        "b8d8ef1e12f98b4f2a9f47abc9765da0640b182b6c5d9b92f0c1a1f2f1e02e5c",
        "Apache-2.0",
        "data/raw/hexagon/truthfulqa/TruthfulQA.csv",
    ),
    PinnedSource(
        "hexagon-bbq",
        "BBQ",
        "bea11bd97d79217245b5871acd247b9d6eb24598",
        "https://github.com/nyu-mll/BBQ/archive/bea11bd97d79217245b5871acd247b9d6eb24598.tar.gz",
        "2fa966b0395a0ce9248700e10e4b72cf47e02cebd34a06105f35ec78ca39dc95",
        "CC-BY-4.0",
        "data/raw/hexagon/bbq/archive.tar.gz",
    ),
)


def select_keys(keys: Iterable[str], *, count: int, seed: str) -> list[str]:
    """按固定种子哈希选择稳定来源键，结果不受上游遍历顺序影响。

    Args:
        keys: 候选来源键；重复值只计作一个候选。
        count: 需要返回的唯一来源键数量。
        seed: 参与每个来源键 SHA-256 排名的固定协议种子。

    Returns:
        按二进制 SHA-256 从小到大排列的前 ``count`` 个来源键。

    Raises:
        ValueError: 数量为负数，或唯一候选不足以满足固定选择数量时抛出。
    """
    if count < 0:
        raise ValueError("source key count cannot be negative")
    # 先去重再哈希，防止上游重复记录通过重复计数污染固定样本数量。
    ranked = sorted(
        set(keys),
        key=lambda key: hashlib.sha256(f"{seed}{key}".encode()).digest(),
    )
    if len(ranked) < count:
        raise ValueError(f"not enough source keys: required {count}, found {len(ranked)}")
    return ranked[:count]


def gsm8k_prompt(question: str) -> str:
    """把官方 GSM8K 问题包装为现有数值答案协议使用的英文提示。

    Args:
        question: 官方测试集中的英文问题正文。

    Returns:
        要送入模型的完整英文数值推理提示。
    """
    return (
        "Solve the following grade-school math problem. "
        "Return the final answer as a single number.\n\n"
        f"Problem: {question}\n\nFinal answer:"
    )


def mmlu_prompt(subject: str, values: list[str]) -> str:
    """把一条 MMLU 六列记录转换为现有单字母选择题英文提示。

    Args:
        subject: 官方测试 CSV 文件名中的学科标识。
        values: 依次包含问题、A 至 D 选项和答案的六列字符串。

    Returns:
        要送入模型的完整英文选择题提示。

    Raises:
        ValueError: 记录不足六列，无法构造四选一题目时抛出。
    """
    if len(values) < 6:
        raise ValueError("MMLU row must contain question, four options, and answer")
    question, option_a, option_b, option_c, option_d = values[:5]
    # 格式与原有 MMLU 加载器保持一致，避免 Hexagon 接入改变历史提示语义。
    return (
        "Answer the multiple-choice question. "
        "Return only one letter: A, B, C, or D.\n\n"
        f"Subject: {subject.replace('_', ' ')}\n"
        f"Question: {question}\n"
        f"A. {option_a}\nB. {option_b}\nC. {option_c}\nD. {option_d}\n\nAnswer:"
    )


def extract_gsm8k_answer(answer: str) -> str:
    """提取 GSM8K 官方 ``####`` 标记后的标准数值答案。

    Args:
        answer: 包含官方推理过程和最终答案标记的原始字符串。

    Returns:
        去除千位分隔符的有符号整数或小数字符串。

    Raises:
        ValueError: 文本中不存在符合官方格式的最终数值时抛出。
    """
    match = re.search(r"####\s*([-+]?\d[\d,]*(?:\.\d+)?)", answer)
    if not match:
        raise ValueError(f"cannot extract GSM8K final answer: {answer[:120]}")
    return match.group(1).replace(",", "")


def _add_row(rows: dict[str, NormalizedSourceRow], row: NormalizedSourceRow) -> None:
    """把规范化记录加入来源映射，并拒绝会让清单选择歧义的重复键。

    Args:
        rows: 当前来源已经解析出的来源键映射。
        row: 准备加入映射的单条规范化记录。

    Raises:
        ValueError: 同一来源键已经出现时抛出。
    """
    if row.source_key in rows:
        raise ValueError(f"duplicate source key: {row.source_key}")
    rows[row.source_key] = row


def _required_text(payload: Mapping[str, object], field: str, source_key: str) -> str:
    """读取官方记录中的必填字符串，并在格式漂移时给出可定位错误。

    Args:
        payload: 已确认是对象的官方来源记录。
        field: 需要读取且不能为空的字段名。
        source_key: 用于错误信息定位原始记录的稳定选择器。

    Returns:
        保持官方内容不变的非空字符串。

    Raises:
        ValueError: 字段缺失、不是字符串或为空时抛出。
    """
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"source row {source_key} is missing {field}")
    return value


def _archive_text(archive: tarfile.TarFile, member: tarfile.TarInfo) -> io.TextIOWrapper:
    """把普通 tar 成员包装为 UTF-8 文本流，不把归档内容解压到磁盘。

    Args:
        archive: 已打开的固定来源归档。
        member: 已由调用方筛选出的普通文件成员。

    Returns:
        可由 CSV 或 JSON 解析器逐行读取的文本流。

    Raises:
        ValueError: 归档成员没有可读文件流时抛出。
    """
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"archive member is unreadable: {member.name}")
    return io.TextIOWrapper(stream, encoding="utf-8", newline="")


def parse_mmlu_rows(path: Path) -> dict[str, NormalizedSourceRow]:
    """直接读取固定 MMLU 归档中的 test CSV，并生成 ``subject:row`` 映射。

    Args:
        path: 已通过固定文件摘要校验的官方 ``data.tar`` 路径。

    Returns:
        覆盖归档内全部有效测试记录的规范化来源键映射。

    Raises:
        ValueError: 来源键重复、测试记录列数不足或归档没有测试数据时抛出。
        OSError: 归档文件不可读时保留底层文件系统错误。
    """
    rows: dict[str, NormalizedSourceRow] = {}
    pattern = re.compile(r"(?:^|/)data/test/([^/]+)_test\.csv$")
    with tarfile.open(path) as archive:
        # 成员排序消除不同 tar 写入顺序对解析和错误定位的影响。
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            match = pattern.search(member.name)
            if match is None or not member.isfile():
                continue
            subject = match.group(1)
            with _archive_text(archive, member) as stream:
                _parse_mmlu_member(rows, subject, stream)
    if not rows:
        raise ValueError("MMLU archive contains no test rows")
    return rows


def _parse_mmlu_member(
    rows: dict[str, NormalizedSourceRow], subject: str, stream: Iterable[str]
) -> None:
    """解析一个 MMLU 学科 CSV 成员，并把有效记录加入共享来源映射。

    Args:
        rows: 整个归档共享的规范化结果映射。
        subject: 由归档成员文件名解析出的官方学科标识。
        stream: 不落盘解压的 UTF-8 CSV 文本行迭代器。

    Raises:
        ValueError: 任一测试记录不足六列或出现重复来源键时抛出。
    """
    for index, values in enumerate(csv.reader(stream), start=1):
        source_key = f"{subject}:{index}"
        prompt = mmlu_prompt(subject, values)
        reference = values[5].strip().upper()
        # 原题与选项保留在元数据中，便于清单构建翻译和失败样本审计。
        metadata: dict[str, object] = {
            "subject": subject,
            "question": values[0],
            "options": values[1:5],
        }
        _add_row(
            rows,
            NormalizedSourceRow(source_key, prompt, reference, "choice_letter", metadata),
        )


def parse_gsm8k_rows(path: Path) -> dict[str, NormalizedSourceRow]:
    """逐物理行解析固定 GSM8K test JSONL，并生成 ``test.jsonl:line`` 映射。

    Args:
        path: 已通过固定文件摘要校验的官方测试 JSONL 路径。

    Returns:
        覆盖每条非空官方记录的规范化来源键映射。

    Raises:
        ValueError: JSON 记录不是对象、字段缺失、答案无效或来源键重复时抛出。
        OSError: 固定来源文件不可读时保留底层错误。
    """
    rows: dict[str, NormalizedSourceRow] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"GSM8K row {line_number} must be an object")
            source_key = f"test.jsonl:{line_number}"
            question = _required_text(payload, "question", source_key)
            answer = _required_text(payload, "answer", source_key)
            metadata: dict[str, object] = {"question": question}
            _add_row(
                rows,
                NormalizedSourceRow(
                    source_key,
                    gsm8k_prompt(question),
                    extract_gsm8k_answer(answer),
                    "numeric_exact_match",
                    metadata,
                ),
            )
    return rows


def parse_bbh_rows(path: Path) -> dict[str, NormalizedSourceRow]:
    """直接读取 BBH 归档任务 JSON，并生成 ``task:index`` 一基来源键映射。

    Args:
        path: 已通过固定文件摘要校验的官方 BBH tar.gz 路径。

    Returns:
        覆盖归档内全部任务示例的规范化来源键映射。

    Raises:
        ValueError: 任务结构、输入、目标或来源键不符合固定格式时抛出。
        OSError: 归档文件不可读时保留底层错误。
    """
    rows: dict[str, NormalizedSourceRow] = {}
    pattern = re.compile(r"(?:^|/)bbh/([^/]+)\.json$")
    with tarfile.open(path) as archive:
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            match = pattern.search(member.name)
            if match is None or not member.isfile():
                continue
            with _archive_text(archive, member) as stream:
                payload = json.load(stream)
            _parse_bbh_task(rows, match.group(1), payload)
    return rows


def _parse_bbh_task(rows: dict[str, NormalizedSourceRow], task: str, payload: object) -> None:
    """校验一个 BBH 任务对象并保留每条示例的官方目标。

    Args:
        rows: 整个 BBH 归档共享的规范化结果映射。
        task: 由归档成员文件名得到的官方任务名。
        payload: 从该任务 JSON 成员解析出的未知对象。

    Raises:
        ValueError: 任务缺少示例数组、记录字段无效或来源键重复时抛出。
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("examples"), list):
        raise ValueError(f"BBH task {task} is missing examples")
    examples = payload["examples"]
    for index, example in enumerate(examples, start=1):
        source_key = f"{task}:{index}"
        if not isinstance(example, dict):
            raise ValueError(f"BBH row {source_key} must be an object")
        input_text = _required_text(example, "input", source_key)
        target = _required_text(example, "target", source_key)
        metadata: dict[str, object] = {"task": task, "official_input": input_text}
        _add_row(
            rows,
            NormalizedSourceRow(
                source_key, f"{input_text}\n\nAnswer:", target, "exact_match", metadata
            ),
        )


def parse_ifeval_rows(path: Path) -> dict[str, NormalizedSourceRow]:
    """发现固定 IFEval JSONL 的英文提示与官方规则参数，供后续严格评分使用。

    Args:
        path: 已通过固定文件摘要校验的官方 IFEval JSONL 路径。

    Returns:
        以十进制整数键索引的规范化 IFEval 记录映射。

    Raises:
        ValueError: 键、提示、规则列表、参数列表无效或来源键重复时抛出。
        OSError: 固定来源文件不可读时保留底层错误。
    """
    rows: dict[str, NormalizedSourceRow] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            payload = json.loads(line)
            if not isinstance(payload, dict) or not isinstance(payload.get("key"), int):
                raise ValueError(f"IFEval row {line_number} has invalid key")
            source_key = str(payload["key"])
            prompt = _required_text(payload, "prompt", source_key)
            instruction_ids = payload.get("instruction_id_list")
            kwargs = payload.get("kwargs")
            if not isinstance(instruction_ids, list) or not isinstance(kwargs, list):
                raise ValueError(f"IFEval row {source_key} has invalid rules")
            # 只接收可传给评测器的一一对应对象，避免上游格式漂移在运行期才暴露。
            if len(instruction_ids) != len(kwargs) or any(
                not isinstance(item, str) for item in instruction_ids
            ):
                raise ValueError(f"IFEval row {source_key} has invalid rules")
            if any(not isinstance(item, dict) for item in kwargs):
                raise ValueError(f"IFEval row {source_key} has invalid rules")
            metadata: dict[str, object] = {
                "instruction_id_list": instruction_ids,
                "kwargs": kwargs,
            }
            _add_row(
                rows,
                NormalizedSourceRow(source_key, prompt, "", "ifeval_strict", metadata),
            )
    return rows


def parse_humaneval_rows(path: Path) -> dict[str, NormalizedSourceRow]:
    """直接读取 HumanEval gzip JSONL，发现固定任务提示和标准实现摘要输入。

    Args:
        path: 已通过固定文件摘要校验的官方 HumanEval JSONL gzip 路径。

    Returns:
        以官方 ``HumanEval/N`` 任务 ID 索引的规范化记录映射。

    Raises:
        ValueError: 记录不是对象、任务字段缺失或来源键重复时抛出。
        OSError: gzip 来源文件不可读时保留底层错误。
    """
    rows: dict[str, NormalizedSourceRow] = {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"HumanEval row {line_number} must be an object")
            source_key = _required_text(payload, "task_id", str(line_number))
            prompt = _required_text(payload, "prompt", source_key)
            canonical = _required_text(payload, "canonical_solution", source_key)
            entry_point = _required_text(payload, "entry_point", source_key)
            metadata: dict[str, object] = {"entry_point": entry_point}
            _add_row(
                rows,
                NormalizedSourceRow(source_key, prompt, canonical, "pass@1", metadata),
            )
    return rows


def load_selected_humaneval_rows(
    path: Path,
    source_keys: Iterable[str],
    *,
    expected_sha256: str | None = None,
) -> dict[str, HumanEvalSourceRow]:
    """从 gzip 流中只保留固定选择的 HumanEval 执行记录，不解压到磁盘。

    Args:
        path: 已通过固定摘要校验的官方 HumanEval gzip JSONL 路径。
        source_keys: 冻结清单要求加载的唯一官方 ``HumanEval/N`` 标识。
        expected_sha256: 生产加载时必须再次匹配的固定 gzip 摘要；离线夹具可省略。

    Returns:
        仅包含选中 ID 的执行记录映射；标准实现和隐藏测试不会进入领域样本元数据。

    Raises:
        ValueError: 摘要、选择器、来源记录或选中记录不符合固定协议时抛出。
        OSError: gzip 来源文件不可读时保留底层文件系统错误。
    """
    if expected_sha256 is not None:
        actual_sha256 = _file_sha256(path)
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"source SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
            )
    requested = tuple(source_keys)
    if len(requested) != len(set(requested)):
        raise ValueError("duplicate HumanEval source selectors")
    selected = set(requested)
    rows: dict[str, HumanEvalSourceRow] = {}

    # 逐行只暂存命中的五条记录，避免完整测试集的标准实现和隐藏测试长期驻留内存。
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"HumanEval row {line_number} must be an object")
            source_key = _required_text(payload, "task_id", str(line_number))
            if source_key not in selected:
                continue
            if source_key in rows:
                raise ValueError(f"duplicate HumanEval source key: {source_key}")
            # 隐藏字段只写入专用记录，现有文本样本规范化路径继续看不到 ``test``。
            rows[source_key] = HumanEvalSourceRow(
                source_key=source_key,
                prompt=_required_text(payload, "prompt", source_key),
                canonical_solution=_required_text(payload, "canonical_solution", source_key),
                test=_required_text(payload, "test", source_key),
                entry_point=_required_text(payload, "entry_point", source_key),
            )

    # 缺失选择器必须在模型调用前失败，不能用其他题目或空测试静默补位。
    missing = selected.difference(rows)
    if missing:
        raise ValueError(f"missing HumanEval source selectors: {', '.join(sorted(missing))}")
    return rows


def parse_truthfulqa_rows(
    path: Path,
    option_orders: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, NormalizedSourceRow]:
    """读取 TruthfulQA CSV，并按冻结排列构造官方正确/错误答案二选一输入。

    Args:
        path: 已通过固定文件摘要校验的官方 TruthfulQA CSV 路径。
        option_orders: 按来源键指定正确、错误答案标签的 ``AB`` 或 ``BA`` 排列。

    Returns:
        以 ``TruthfulQA.csv:row`` 一基键索引的二选一规范化记录映射。

    Raises:
        ValueError: 必填列缺失、选项排列非法或来源键重复时抛出。
        OSError: CSV 文件不可读时保留底层错误。
    """
    rows: dict[str, NormalizedSourceRow] = {}
    option_orders = option_orders or {}
    with path.open("r", encoding="utf-8", newline="") as stream:
        for index, payload in enumerate(csv.DictReader(stream), start=1):
            source_key = f"TruthfulQA.csv:{index}"
            _add_truthfulqa_row(rows, payload, source_key, option_orders.get(source_key))
    return rows


def _add_truthfulqa_row(
    rows: dict[str, NormalizedSourceRow],
    payload: Mapping[str, object],
    source_key: str,
    option_order: tuple[str, ...] | None,
) -> None:
    """按单条冻结选项顺序构造 TruthfulQA 输入，并保留类别供分层选择。

    Args:
        rows: 当前 CSV 已解析出的规范化结果映射。
        payload: DictReader 产生的单条官方 CSV 记录。
        source_key: 当前数据行的一基稳定来源键。
        option_order: 正确、错误答案各自占用的标签，缺省使用 ``AB``。

    Raises:
        ValueError: 选项顺序不是严格的 ``AB`` 或 ``BA`` 时抛出。
    """
    order = option_order or ("A", "B")
    if order not in (("A", "B"), ("B", "A")):
        raise ValueError(f"invalid TruthfulQA option order for {source_key}")
    question = _required_text(payload, "Question", source_key)
    best = _required_text(payload, "Best Answer", source_key)
    incorrect = _required_text(payload, "Best Incorrect Answer", source_key)
    category = _required_text(payload, "Category", source_key)
    options = {order[0]: best, order[1]: incorrect}
    # 正确答案标签由冻结排列直接决定，避免运行时随机交换选项。
    prompt = (
        "Answer the multiple-choice question. Return only one letter: A or B.\n\n"
        f"Question: {question}\nA. {options['A']}\nB. {options['B']}\n\nAnswer:"
    )
    metadata: dict[str, object] = {"category": category, "question": question}
    _add_row(rows, NormalizedSourceRow(source_key, prompt, order[0], "choice_letter", metadata))


def parse_bbq_rows(path: Path) -> dict[str, NormalizedSourceRow]:
    """直接读取 BBQ 归档 JSONL，并生成 ``category:example_id`` 来源键映射。

    Args:
        path: 已通过固定文件摘要校验的官方 BBQ tar.gz 路径。

    Returns:
        保留上下文、题目、三个选项和标签的规范化来源记录映射。

    Raises:
        ValueError: JSON 结构、评分字段或来源键无效或重复时抛出。
        OSError: 归档文件不可读时保留底层错误。
    """
    rows: dict[str, NormalizedSourceRow] = {}
    pattern = re.compile(r"(?:^|/)data/[^/]+\.jsonl$")
    with tarfile.open(path) as archive:
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            if pattern.search(member.name) is None or not member.isfile():
                continue
            with _archive_text(archive, member) as stream:
                for line_number, line in enumerate(stream, start=1):
                    _add_bbq_row(rows, json.loads(line), member.name, line_number)
    return rows


def _add_bbq_row(
    rows: dict[str, NormalizedSourceRow], payload: object, member_name: str, line_number: int
) -> None:
    """校验单条 BBQ JSONL 记录，并构造固定三选一英文提示。

    Args:
        rows: 整个 BBQ 归档共享的规范化结果映射。
        payload: 从 JSONL 解析出的未知单条对象。
        member_name: 用于错误定位的归档成员路径。
        line_number: 成员内部的一基物理行号。

    Raises:
        ValueError: 必填字段、标签范围或来源键不符合官方格式时抛出。
    """
    if not isinstance(payload, dict):
        raise ValueError(f"BBQ row {member_name}:{line_number} must be an object")
    example_id = payload.get("example_id")
    label = payload.get("label")
    if not isinstance(example_id, int) or label not in (0, 1, 2):
        raise ValueError(f"BBQ row {member_name}:{line_number} has invalid scoring fields")
    category = _required_text(payload, "category", str(example_id))
    source_key = f"{category}:{example_id}"
    context = _required_text(payload, "context", source_key)
    question = _required_text(payload, "question", source_key)
    answers = [_required_text(payload, f"ans{index}", source_key) for index in range(3)]
    _required_text(payload, "context_condition", source_key)
    # 复制完整官方对象，保留后续偏见指标审计需要的附加字段而不改写其类型。
    metadata: dict[str, object] = dict(payload)
    prompt = (
        "Answer the multiple-choice question. Return only one letter: A, B, or C.\n\n"
        f"Context: {context}\nQuestion: {question}\n"
        f"A. {answers[0]}\nB. {answers[1]}\nC. {answers[2]}\n\nAnswer:"
    )
    reference = ("A", "B", "C")[label]
    _add_row(rows, NormalizedSourceRow(source_key, prompt, reference, "choice_letter", metadata))


def load_hexagon_source_rows(
    benchmark_id: str,
    path: Path,
    *,
    option_orders: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, NormalizedSourceRow]:
    """按 Hexagon Benchmark ID 分派固定来源解析器并返回统一记录映射。

    Args:
        benchmark_id: 七个已注册 Hexagon 来源切片之一。
        path: 对应来源已校验的本地固定资产路径。
        option_orders: TruthfulQA 清单冻结的正确/错误答案标签排列。

    Returns:
        可由固定清单来源键直接索引的规范化英文记录映射。

    Raises:
        KeyError: Benchmark ID 没有对应固定解析器时抛出。
        ValueError: 具体来源结构或选择器不符合固定协议时抛出。
    """
    parsers: dict[str, Callable[[Path], dict[str, NormalizedSourceRow]]] = {
        "hexagon-mmlu": parse_mmlu_rows,
        "hexagon-ifeval": parse_ifeval_rows,
        "hexagon-gsm8k": parse_gsm8k_rows,
        "hexagon-bbh": parse_bbh_rows,
        "hexagon-humaneval": parse_humaneval_rows,
        "hexagon-bbq": parse_bbq_rows,
    }
    if benchmark_id == "hexagon-truthfulqa":
        return parse_truthfulqa_rows(path, option_orders)
    try:
        parser = parsers[benchmark_id]
    except KeyError as exc:
        raise KeyError(f"unsupported Hexagon source parser: {benchmark_id}") from exc
    return parser(path)


def hexagon_source_specs() -> dict[str, PinnedSource]:
    """返回按 Benchmark ID 索引的固定来源规格副本。

    Returns:
        键为七个稳定 Hexagon Benchmark ID，值为不可变固定来源记录的新字典。
    """
    # 新字典避免调用方替换条目影响后续缓存准备，值本身由冻结数据类保护。
    return {source.benchmark_id: source for source in PINNED_SOURCES}


def prepare_hexagon_dataset(name: str, root: Path | str = ".", force: bool = False) -> Path:
    """校验或下载指定 Hexagon 资产，并在通过固定摘要后返回缓存文件。

    Args:
        name: 七个已注册 Hexagon Benchmark 的稳定 ID。
        root: 数据缓存的项目根目录或隔离测试目录。
        force: 为真时始终下载候选文件；为假时仅复用摘要一致的既有缓存。

    Returns:
        通过 SHA-256 校验的固定原始资产本地路径。

    Raises:
        KeyError: ``name`` 不在固定来源目录中。
        ValueError: 既有缓存或下载候选的 SHA-256 与固定来源记录不一致。
        OSError: 下载、临时文件或原子替换发生文件系统错误。
    """
    source = hexagon_source_specs()[name]
    destination = Path(root) / source.cache_path
    # 默认只接受完整性已被固定摘要证明的缓存，避免静默使用损坏或漂移的旧文件。
    if destination.exists() and not force:
        actual = _file_sha256(destination)
        if actual != source.sha256:
            raise ValueError(f"source SHA-256 mismatch: expected {source.sha256}, got {actual}")
        return destination

    # 下载始终写入同目录候选文件；候选摘要通过前不会触及已有可用缓存。
    return _install_pinned_file(
        destination,
        expected_sha256=source.sha256,
        download=lambda candidate: urlretrieve(source.url, candidate),
    )


def _install_pinned_file(
    destination: Path,
    *,
    expected_sha256: str,
    download: Callable[[Path], object],
) -> Path:
    """先校验候选文件，再以同目录原子替换安装固定来源资产。

    Args:
        destination: 通过校验后要替换的正式缓存文件路径。
        expected_sha256: 固定来源记录中的小写 SHA-256 十六进制摘要。
        download: 将候选路径写满下载内容的可调用对象。

    Returns:
        已由候选资产原子替换完成的正式缓存路径。

    Raises:
        ValueError: 候选文件摘要与固定来源摘要不一致。
        OSError: 创建候选文件、下载或替换缓存失败。
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 候选文件位于目标父目录，保证 ``replace`` 不跨文件系统且替换操作可原子完成。
    with NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        candidate = Path(stream.name)
    try:
        download(candidate)
        actual = _file_sha256(candidate)
        if actual != expected_sha256:
            raise ValueError(f"source SHA-256 mismatch: expected {expected_sha256}, got {actual}")
        # 仅在校验成功后替换旧缓存，因此下载失败或摘要不符不会损坏既有资产。
        candidate.replace(destination)
    finally:
        candidate.unlink(missing_ok=True)
    return destination


def _file_sha256(path: Path) -> str:
    """以分块方式计算文件 SHA-256，避免大型归档被整体载入内存。

    Args:
        path: 需要验证的候选或既有缓存文件。

    Returns:
        文件字节内容对应的小写 SHA-256 十六进制摘要。

    Raises:
        OSError: 文件不可读或读取过程中发生底层 I/O 错误。
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
