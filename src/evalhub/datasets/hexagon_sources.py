"""下载并校验 Hexagon Benchmark 所需的固定版本官方原始资产。"""

import hashlib
from collections.abc import Callable
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
