# EvalHub Hexagon Benchmark v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fixed 60-sample, six-dimension model suite sourced from seven pinned professional public Benchmarks, score only official English inputs, and show Chinese translations for human inspection.

**Architecture:** Keep the existing Benchmark Registry, persistent workflow, model adapters, evaluator interface, and capability aggregation. Add pinned source downloads plus a versioned 60-row selection manifest, normalize each official format into the existing sample boundary, use a dedicated IFEval strict evaluator and Docker-only HumanEval runner, then preserve source and translation metadata through SQLite and the existing result UI.

**Tech Stack:** Python 3.11 standard library, existing pytest/Ruff toolchain, Docker for HumanEval isolation, existing React 19/TypeScript/Vite frontend, existing SQLite task repository.

## Global Constraints

- Suite ID is exactly `evalhub-hexagon-v1`; protocol version is exactly `1.0.0`.
- Dimensions remain exactly `knowledge`, `instruction_following`, `mathematics`, `reasoning`, `coding`, and `safety_trust`.
- Complete runs score exactly 60 English inputs: 10 per dimension.
- Source slices contain exactly 10 MMLU, 10 IFEval, 10 GSM8K, 10 BBH, 10 HumanEval, 5 TruthfulQA, and 5 BBQ samples.
- Chinese text is display-only metadata. No `input_zh` or `reference_zh` value may reach a model adapter or evaluator.
- Selection seed is exactly `evalhub-hexagon-v1`; selected source keys are frozen in the committed manifest.
- Full-suite comparisons require `sample_mode=all`, all six assessed dimensions, and identical suite/source/manifest/prompt revisions.
- Text generation uses `temperature=0`; HumanEval generates one candidate and reports Pass@1.
- HumanEval code runs only in Docker with no network, no host mount, a read-only root, dropped capabilities, and resource limits.
- No new Python or frontend dependency is added; source preparation uses `urllib`, `hashlib`, `csv`, `json`, `gzip`, and `tarfile`.
- Unit tests use local temporary fixtures only; they do not download public data, call Ollama, or require Docker.
- All changed Python functions and methods follow the repository's detailed Chinese docstring and comment-density rules.
- Existing `llm-industry-core-v1`, standalone GSM8K/MMLU, and Agent Coding Mini behavior remains backward compatible.

### Pinned source assets

| Source | Revision | License | Download URL | SHA-256 |
| --- | --- | --- | --- | --- |
| MMLU | official `data.tar` snapshot | MIT | `https://people.eecs.berkeley.edu/~hendrycks/data.tar` | `bec563ba4bac1d6aaf04141cd7d1605d7a5ca833e38f994051e818489592989b` |
| IFEval | `8dadc6c56e2c2e51a9dd7e0d4bf2840922b4b6c0` | CC-BY-4.0 data / Apache-2.0 code | `https://raw.githubusercontent.com/google-research/google-research/8dadc6c56e2c2e51a9dd7e0d4bf2840922b4b6c0/instruction_following_eval/data/input_data.jsonl` | `67ffeee0fcb87c317c5b08a2de85557b4a7e96ada6178aa645b4954fe4b53d49` |
| GSM8K | `3101c7d5072418e28b9008a6636bde82a006892c` | MIT | `https://raw.githubusercontent.com/openai/grade-school-math/3101c7d5072418e28b9008a6636bde82a006892c/grade_school_math/data/test.jsonl` | `3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14` |
| BBH | `9ee07bd481feebf959a6b59d61ea57bdcf30964d` | MIT | `https://github.com/suzgunmirac/BIG-Bench-Hard/archive/9ee07bd481feebf959a6b59d61ea57bdcf30964d.tar.gz` | `0bb15e11935747f7cfa42ef2e02254b70f9c9e545f6dabfd374dec3b6ba95bbc` |
| HumanEval | `6d43fb980f9fee3c892a914eda09951f772ad10d` | MIT | `https://raw.githubusercontent.com/openai/human-eval/6d43fb980f9fee3c892a914eda09951f772ad10d/data/HumanEval.jsonl.gz` | `b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef` |
| TruthfulQA | `d71c110897f5d31c5d7f309e7bc316c152f6f031` | Apache-2.0 | `https://raw.githubusercontent.com/sylinrl/TruthfulQA/d71c110897f5d31c5d7f309e7bc316c152f6f031/TruthfulQA.csv` | `b8d8ef1e12f98b4f2a9f47abc9765da0640b182b6c5d9b92f0c1a1f2f1e02e5c` |
| BBQ | `bea11bd97d79217245b5871acd247b9d6eb24598` | CC-BY-4.0 | `https://github.com/nyu-mll/BBQ/archive/bea11bd97d79217245b5871acd247b9d6eb24598.tar.gz` | `2fa966b0395a0ce9248700e10e4b72cf47e02cebd34a06105f35ec78ca39dc95` |

### Exact selection strata

Within each named stratum, select the row with the lowest binary
`SHA-256("evalhub-hexagon-v1" + source_key)` value. GSM8K is the sole exception: select the
lowest ten values over the complete test split. Source-file line numbers and task-local indexes are
one-based.

- MMLU subjects: `abstract_algebra`, `anatomy`, `business_ethics`,
  `college_computer_science`, `econometrics`, `high_school_world_history`, `international_law`,
  `machine_learning`, `professional_medicine`, and `sociology`.
- IFEval uses these already-derived single-rule rows: `32` (`punctuation:no_comma`), `1759`
  (`detectable_content:postscript`), `2829` (`startend:quotation`), `321`
  (`detectable_format:json_format`), `3221` (`detectable_content:number_placeholders`), `2832`
  (`detectable_format:number_bullet_lists`), `2253`
  (`detectable_format:number_highlighted_sections`), `2925`
  (`detectable_format:multiple_sections`), `1551` (`detectable_format:title`), and `1659`
  (`startend:end_checker`). The builder must verify each key still contains exactly the named rule.
- BBH tasks: `boolean_expressions`, `causal_judgement`, `date_understanding`,
  `disambiguation_qa`, `formal_fallacies`, `logical_deduction_five_objects`,
  `multistep_arithmetic_two`, `object_counting`, `temporal_sequences`, and
  `tracking_shuffled_objects_five_objects`.
- HumanEval task IDs: `HumanEval/126`, `HumanEval/84`, `HumanEval/108`, `HumanEval/30`,
  `HumanEval/24`, `HumanEval/54`, `HumanEval/158`, `HumanEval/131`, `HumanEval/123`, and
  `HumanEval/63`.
- TruthfulQA categories: `Misconceptions`, `Health`, `Conspiracies`, `Stereotypes`, and
  `Superstitions`. In final manifest order, correct/incorrect option order alternates as `AB`, `BA`,
  `AB`, `BA`, `AB`; the manifest stores the order explicitly.
- BBQ category/context pairs: `Age/ambig`, `Disability_status/disambig`,
  `Gender_identity/ambig`, `Race_ethnicity/disambig`, and `Religion/ambig`.

---

## File Structure

- `src/evalhub/benchmarks/registry.py`: add seven Hexagon Benchmark specs and the new Suite without changing the existing core Suite.
- `src/evalhub/benchmarks/readiness.py`: report native, unsupported, and Docker HumanEval executor readiness through one shared function.
- `src/evalhub/benchmarks/humaneval.py`: generate one completion per selected HumanEval problem and score it through the Docker boundary.
- `src/evalhub/datasets/hexagon_manifest.py`: parse and validate pinned source metadata, selected source keys, and display-only translations.
- `src/evalhub/datasets/hexagon_sources.py`: atomically download pinned files and normalize MMLU, GSM8K, BBH, TruthfulQA, and BBQ rows.
- `src/evalhub/datasets/manifests/hexagon_v1.json`: freeze all 60 source selectors and Chinese translations.
- `scripts/build_hexagon_manifest.py`: reproduce the deterministic, stratified source-key selection before the manifest is frozen.
- `src/evalhub/evaluators/ifeval.py`: implement the selected official IFEval strict rules without extra dependencies.
- `docker/hexagon-humaneval/Dockerfile`: build the fixed, non-root Python verifier image.
- `docker/hexagon-humaneval/verify.py`: execute one candidate and hidden test payload inside the restricted container.
- `scripts/build_humaneval_image.sh`: build the exact Docker image used by readiness checks.
- Existing dataset, runner, task, server, and frontend files: connect the new source slices and preserve bilingual provenance.

---

### Task 1: Register the Hexagon Suite and Source Contracts

**Files:**
- Create: `src/evalhub/datasets/hexagon_manifest.py`
- Create: `tests/test_hexagon_manifest.py`
- Modify: `src/evalhub/benchmarks/registry.py`
- Modify: `src/evalhub/benchmarks/__init__.py`
- Modify: `tests/test_benchmark_registry.py`

**Interfaces:**
- Consumes: existing `BenchmarkSpec`, `BenchmarkSuiteSpec`, `Capability`, `ExecutorKind`, and `NormalizationKind`.
- Produces: `HexagonSampleSpec`, `hexagon_manifest() -> tuple[HexagonSampleSpec, ...]`, seven `hexagon-*` Benchmark specs, and `get_suite_spec("evalhub-hexagon-v1")`.

- [ ] **Step 1: Write failing Registry and manifest-contract tests**

```python
def test_hexagon_suite_has_fixed_source_counts_and_six_dimensions() -> None:
    suite = get_suite_spec("evalhub-hexagon-v1")
    specs = [get_benchmark_spec(item) for item in suite.benchmark_ids]

    assert [item.expected_sample_count for item in specs] == [10, 10, 10, 10, 10, 5, 5]
    assert {item.capability for item in specs} == set(Capability)
    assert all(item.normalization == NormalizationKind.SCALE_100 for item in specs)
    assert sum(item.weight for item in specs if item.capability == Capability.SAFETY_TRUST) == 1


def test_hexagon_manifest_requires_complete_bilingual_provenance(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"version": "1.0.0", "samples": [{"id": "broken"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manifest sample is missing"):
        load_hexagon_manifest(path)
```

- [ ] **Step 2: Run the focused tests and confirm the contracts are missing**

Run: `.venv/bin/python -m pytest tests/test_benchmark_registry.py tests/test_hexagon_manifest.py -q`

Expected: FAIL importing `evalhub.datasets.hexagon_manifest` and resolving `evalhub-hexagon-v1`.

- [ ] **Step 3: Add immutable manifest models and strict parsing**

```python
@dataclass(frozen=True)
class HexagonSampleSpec:
    id: str
    benchmark_id: str
    capability: Capability
    source_key: str
    input_sha256: str
    reference_sha256: str
    input_zh: str
    reference_zh: str | None
    input_zh_sha256: str
    reference_zh_sha256: str | None
    translation_version: str
    option_order: tuple[str, ...] | None = None


def load_hexagon_manifest(path: Path) -> tuple[HexagonSampleSpec, ...]:
    """读取并验证固定选择清单，拒绝缺字段、重复 ID 和空翻译。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = tuple(_parse_manifest_row(item) for item in payload["samples"])
    _validate_manifest(rows)
    return rows
```

`_validate_manifest()` must enforce exactly 60 unique IDs; capability counts of 10 each; source-slice
counts of 10, 10, 10, 10, 10, 5, 5; non-empty `source_key`, `input_zh`, and
`translation_version`; valid lowercase SHA-256 fields; the exact selection strata above; and
benchmark IDs that belong to `evalhub-hexagon-v1`. Recompute translation digests while parsing.
Only TruthfulQA rows may set `option_order`, and their five values must be exactly the alternating
orders declared above.

- [ ] **Step 4: Add the seven Registry rows and preserve the existing Suite**

```python
HEXAGON_ROWS: tuple[BenchmarkRow, ...] = (
    ("hexagon-mmlu", "Hexagon · MMLU", Capability.KNOWLEDGE,
     "hendrycks/test", ExecutorKind.NATIVE, "hexagon_mmlu", "acc", None),
    ("hexagon-ifeval", "Hexagon · IFEval", Capability.INSTRUCTION_FOLLOWING,
     "google/IFEval", ExecutorKind.NATIVE, "hexagon_ifeval", "prompt_level_strict_acc", None),
    ("hexagon-gsm8k", "Hexagon · GSM8K", Capability.MATHEMATICS,
     "openai/grade-school-math", ExecutorKind.NATIVE, "hexagon_gsm8k", "exact_match", None),
    ("hexagon-bbh", "Hexagon · BBH", Capability.REASONING,
     "suzgunmirac/BIG-Bench-Hard", ExecutorKind.NATIVE, "hexagon_bbh", "exact_match", None),
    ("hexagon-humaneval", "Hexagon · HumanEval", Capability.CODING,
     "openai/human-eval", ExecutorKind.SANDBOXED_CODE, "hexagon_humaneval", "pass@1", None),
    ("hexagon-truthfulqa", "Hexagon · TruthfulQA", Capability.SAFETY_TRUST,
     "sylinrl/TruthfulQA", ExecutorKind.NATIVE, "hexagon_truthfulqa", "acc", None),
    ("hexagon-bbq", "Hexagon · BBQ", Capability.SAFETY_TRUST,
     "nyu-mll/BBQ", ExecutorKind.NATIVE, "hexagon_bbq", "acc", None),
)
```

Build `BenchmarkSpec` values with expected counts `(10, 10, 10, 10, 10, 5, 5)`,
`NormalizationKind.SCALE_100`, safety weights `0.5`, all other weights `1.0`, and the exact pinned
revisions and licenses from Global Constraints. Add a second entry to `suite_registry()`; do not
change `llm-industry-core-v1` members or version.

- [ ] **Step 5: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_benchmark_registry.py tests/test_hexagon_manifest.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the source contracts**

```bash
git add src/evalhub/benchmarks src/evalhub/datasets/hexagon_manifest.py tests/test_benchmark_registry.py tests/test_hexagon_manifest.py
git commit -m "feat: register professional hexagon suite"
```

---

### Task 2: Download and Verify Pinned Official Assets

**Files:**
- Create: `src/evalhub/datasets/hexagon_sources.py`
- Create: `tests/test_hexagon_sources.py`
- Modify: `src/evalhub/datasets/catalog.py`
- Modify: `src/evalhub/datasets/loaders.py`
- Modify: `src/evalhub/datasets/__init__.py`
- Modify: `tests/test_dataset_refresh.py`

**Interfaces:**
- Consumes: seven Benchmark IDs from Task 1 and the pinned values in Global Constraints.
- Produces: `PinnedSource`, `hexagon_source_specs() -> dict[str, PinnedSource]`, `prepare_hexagon_dataset(name, root, force=False) -> Path`, and dataset-catalog entries for every `hexagon-*` ID.

- [ ] **Step 1: Write failing checksum, cache, and alias tests**

```python
def test_pinned_download_rejects_wrong_digest_without_replacing_cache(tmp_path: Path) -> None:
    destination = tmp_path / "source.jsonl"
    destination.write_bytes(b"known-good")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _install_pinned_file(
            destination,
            expected_sha256=hashlib.sha256(b"expected").hexdigest(),
            download=lambda candidate: candidate.write_bytes(b"corrupt"),
        )

    assert destination.read_bytes() == b"known-good"


def test_hexagon_gsm8k_reuses_official_cache_after_checksum_validation(tmp_path: Path) -> None:
    path = tmp_path / "data/raw/gsm8k/test.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fixture")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        prepare_hexagon_dataset("hexagon-gsm8k", root=tmp_path)
```

- [ ] **Step 2: Run tests and confirm no pinned-source preparation exists**

Run: `.venv/bin/python -m pytest tests/test_hexagon_sources.py tests/test_dataset_refresh.py -q`

Expected: FAIL importing `PinnedSource` and `prepare_hexagon_dataset`.

- [ ] **Step 3: Declare exact source records**

```python
@dataclass(frozen=True)
class PinnedSource:
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
        (
            "https://github.com/suzgunmirac/BIG-Bench-Hard/archive/"
            "9ee07bd481feebf959a6b59d61ea57bdcf30964d.tar.gz"
        ),
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
        (
            "https://github.com/nyu-mll/BBQ/archive/"
            "bea11bd97d79217245b5871acd247b9d6eb24598.tar.gz"
        ),
        "2fa966b0395a0ce9248700e10e4b72cf47e02cebd34a06105f35ec78ca39dc95",
        "CC-BY-4.0",
        "data/raw/hexagon/bbq/archive.tar.gz",
    ),
)
```

The MMLU and GSM8K rows deliberately point at the existing official cache locations so the same
bytes are not downloaded twice. IFEval records the dataset license (`CC-BY-4.0`); the ported
official checker code remains under Apache-2.0 attribution in Task 4.

- [ ] **Step 4: Implement atomic verified installation**

```python
def _install_pinned_file(
    destination: Path,
    *,
    expected_sha256: str,
    download: Callable[[Path], object],
) -> Path:
    """先校验候选文件，再以同目录原子替换安装固定来源资产。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        candidate = Path(stream.name)
    try:
        download(candidate)
        actual = _file_sha256(candidate)
        if actual != expected_sha256:
            raise ValueError(f"source SHA-256 mismatch: expected {expected_sha256}, got {actual}")
        candidate.replace(destination)
    finally:
        candidate.unlink(missing_ok=True)
    return destination
```

When `force=False`, reuse only a cache whose digest matches. When `force=True`, keep the old valid file until the replacement candidate passes. Never accept a moving branch URL.

- [ ] **Step 5: Connect catalog and loader dispatch**

`dataset_catalog()` must expose all seven `hexagon-*` dataset entries using source display names, evaluator types, official homepages, and pinned local paths. `prepare_dataset()` must route these IDs to `prepare_hexagon_dataset()` before the existing GSM8K/MMLU branches.

```python
if name.startswith("hexagon-"):
    return prepare_hexagon_dataset(name, root=root_path, force=force)
```

- [ ] **Step 6: Run source preparation tests**

Run: `.venv/bin/python -m pytest tests/test_hexagon_sources.py tests/test_dataset_refresh.py tests/test_cli_parser.py -q`

Expected: PASS with no network access.

- [ ] **Step 7: Commit pinned source preparation**

```bash
git add src/evalhub/datasets tests/test_hexagon_sources.py tests/test_dataset_refresh.py tests/test_cli_parser.py
git commit -m "feat: verify pinned hexagon sources"
```

---

### Task 3: Freeze the 60-Row Manifest and Normalize Text Sources

**Files:**
- Create: `src/evalhub/datasets/manifests/hexagon_v1.json`
- Create: `scripts/build_hexagon_manifest.py`
- Create: `tests/test_hexagon_text_samples.py`
- Modify: `pyproject.toml`
- Modify: `src/evalhub/datasets/hexagon_manifest.py`
- Modify: `src/evalhub/datasets/hexagon_sources.py`
- Modify: `src/evalhub/datasets/loaders.py`
- Modify: `tests/test_hexagon_manifest.py`

**Interfaces:**
- Consumes: verified source paths from Task 2 and manifest contracts from Task 1.
- Produces: `load_hexagon_samples(name, root, limit=None) -> list[EvaluationSample]`, deterministic source parsers, and a packaged manifest containing exactly 60 selectors and translations.

- [ ] **Step 1: Write failing manifest-selection and normalization tests**

```python
def test_text_sample_keeps_translation_out_of_model_input(tmp_path: Path) -> None:
    install_gsm8k_fixture(tmp_path, line_number=2, question="How many?", answer="#### 7")
    manifest = manifest_with(
        benchmark_id="hexagon-gsm8k",
        source_key="test.jsonl:2",
        input_zh="一共有多少？",
        reference_zh="7",
    )

    samples = load_hexagon_samples(
        "hexagon-gsm8k", root=tmp_path, manifest=manifest
    )

    assert samples[0].input.endswith("Problem: How many?\n\nFinal answer:")
    assert "一共有多少" not in samples[0].input
    assert samples[0].metadata["input_zh"] == "一共有多少？"
    assert samples[0].metadata["source_key"] == "test.jsonl:2"


def test_fixed_hash_selection_is_independent_of_input_order() -> None:
    keys = ["test.jsonl:3", "test.jsonl:1", "test.jsonl:2"]
    expected = select_keys(keys, count=2, seed="evalhub-hexagon-v1")

    assert select_keys(list(reversed(keys)), count=2, seed="evalhub-hexagon-v1") == expected
```

- [ ] **Step 2: Run focused tests and confirm selection/loading is absent**

Run: `.venv/bin/python -m pytest tests/test_hexagon_manifest.py tests/test_hexagon_text_samples.py -q`

Expected: FAIL importing `load_hexagon_samples` and `select_keys`.

- [ ] **Step 3: Implement stable selection and source-key parsing**

```python
def select_keys(keys: Iterable[str], *, count: int, seed: str) -> list[str]:
    """按固定种子哈希排序并返回稳定来源键，不依赖上游输入顺序。"""
    ranked = sorted(
        set(keys),
        key=lambda key: hashlib.sha256(f"{seed}{key}".encode()).digest(),
    )
    if len(ranked) < count:
        raise ValueError(f"not enough source keys: required {count}, found {len(ranked)}")
    return ranked[:count]
```

Use these exact source-key forms: `subject:row` for MMLU, `test.jsonl:line` for GSM8K,
`task:index` for BBH, `TruthfulQA.csv:row` for TruthfulQA, `category:example_id` for BBQ,
the decimal string form of IFEval's integer `key`, and official `HumanEval/N` task IDs for HumanEval.

- [ ] **Step 4: Implement the five text-source parsers**

Each parser returns a mapping from `source_key` to a normalized record and rejects duplicate keys. Read BBH and BBQ members directly with `tarfile.extractfile()`; do not extract archives to disk.

```python
@dataclass(frozen=True)
class NormalizedSourceRow:
    source_key: str
    input: str
    reference: str
    evaluator_type: str
    source_metadata: dict[str, object]


def _selected_samples(
    benchmark_id: str,
    rows: Mapping[str, NormalizedSourceRow],
    manifest: tuple[HexagonSampleSpec, ...],
) -> list[EvaluationSample]:
    """按冻结清单顺序组装英文样本，并把中文内容限制在元数据中。"""
    selected = [item for item in manifest if item.benchmark_id == benchmark_id]
    return [_to_evaluation_sample(item, rows[item.source_key]) for item in selected]
```

`_to_evaluation_sample()` must hash the normalized English input and reference and reject a row when
either value differs from the manifest. TruthfulQA uses the pinned 2025 binary `Best Answer`/`Best
Incorrect Answer` columns with option order frozen by `option_order`. BBQ preserves `context`,
`question`, `ans0..2`, `label`, `context_condition`, and category. BBH preserves task name and exact
official target.

- [ ] **Step 5: Add the reproducible manifest builder**

The script must load all seven verified sources, apply the exact strata in Global Constraints, rank
within each stratum by `SHA-256("evalhub-hexagon-v1" + source_key)`, and write sorted JSON. It must
preserve existing translation fields when rebuilding a manifest with the same source keys, but
recompute all four content digests and reject a preserved translation whose digest no longer matches.
IFEval and HumanEval need only source-key/input/reference discovery here; their scoring paths are added
in Tasks 4 and 5.

```python
def main() -> int:
    """从固定官方缓存生成可审计选择清单，并拒绝未准备的数据源。"""
    args = parser().parse_args()
    rows = build_manifest_rows(Path(args.root))
    write_manifest(Path(args.output), merge_translations(rows, Path(args.output)))
    return 0
```

Run: `.venv/bin/python scripts/build_hexagon_manifest.py --root . --output src/evalhub/datasets/manifests/hexagon_v1.json`

Expected: a deterministic 60-row file with the seven exact source counts. Translate every selected
English question and option into Simplified Chinese inside `input_zh`; preserve code, identifiers,
numbers, and expected option order exactly. Set `translation_version` to `evalhub-zh-v1`, and supply
`reference_zh` wherever revealing the official answer is safe. Compute SHA-256 over the exact UTF-8
bytes of each English and Chinese field, using `null` rather than a digest for a missing
`reference_zh`. Run the builder a second time and confirm the file is byte-for-byte unchanged.

- [ ] **Step 6: Package and load the frozen manifest**

```toml
[tool.setuptools.package-data]
"evalhub.datasets" = ["manifests/*.json"]
```

Use `importlib.resources.files("evalhub.datasets")` to locate the packaged manifest. `load_samples()` must route `hexagon-*` IDs to `load_hexagon_samples()` and apply `limit` only after fixed manifest ordering.

- [ ] **Step 7: Run manifest and text-loader tests**

Run: `.venv/bin/python -m pytest tests/test_hexagon_manifest.py tests/test_hexagon_text_samples.py tests/test_runner.py -q`

Expected: PASS; the tests assert 60 non-empty translations and that English `input` never contains `input_zh`.

- [ ] **Step 8: Commit the frozen text suite data**

```bash
git add pyproject.toml scripts/build_hexagon_manifest.py src/evalhub/datasets tests/test_hexagon_manifest.py tests/test_hexagon_text_samples.py
git commit -m "feat: freeze professional hexagon sample manifest"
```

---

### Task 4: Implement IFEval Prompt-Level Strict Scoring

**Files:**
- Create: `src/evalhub/evaluators/ifeval.py`
- Create: `tests/test_ifeval_evaluator.py`
- Modify: `src/evalhub/evaluators/registry.py`
- Modify: `src/evalhub/evaluators/__init__.py`
- Modify: `src/evalhub/datasets/hexagon_sources.py`
- Modify: `tests/test_hexagon_text_samples.py`

**Interfaces:**
- Consumes: selected IFEval rows and `EvaluationSample.metadata` from Task 3.
- Produces: `IFEvalStrictEvaluator.evaluate(...) -> MetricResult` registered as `ifeval_strict`.

- [ ] **Step 1: Write failing strict-rule tests**

```python
@pytest.mark.parametrize(
    ("instruction_id", "kwargs", "prediction", "expected"),
    [
        ("punctuation:no_comma", {}, "No commas here", 1.0),
        ("punctuation:no_comma", {}, "No, commas here", 0.0),
        ("detectable_format:json_format", {}, '{"ok": true}', 1.0),
        ("detectable_format:json_format", {}, "not json", 0.0),
        ("detectable_content:postscript", {"postscript_marker": "P.S."}, "Body\nP.S. note", 1.0),
        ("detectable_format:multiple_sections", {"section_spliter": "Section", "num_sections": 2}, "Section 1\na\nSection 2\nb", 1.0),
        ("startend:quotation", {}, '"quoted response"', 1.0),
    ],
)
def test_ifeval_strict_rules(
    instruction_id: str,
    kwargs: dict[str, object],
    prediction: str,
    expected: float,
) -> None:
    result = IFEvalStrictEvaluator().evaluate(
        prediction,
        "",
        metadata={"instruction_id_list": [instruction_id], "kwargs": [kwargs]},
    )
    assert result.score == expected


def test_ifeval_requires_every_instruction_to_pass() -> None:
    result = IFEvalStrictEvaluator().evaluate(
        '"contains, comma"',
        "",
        metadata={
            "instruction_id_list": ["startend:quotation", "punctuation:no_comma"],
            "kwargs": [{}, {}],
        },
    )
    assert result.score == 0.0
```

- [ ] **Step 2: Run tests and confirm the evaluator is missing**

Run: `.venv/bin/python -m pytest tests/test_ifeval_evaluator.py -q`

Expected: FAIL importing `IFEvalStrictEvaluator`.

- [ ] **Step 3: Port only the ten selected official rule semantics**

Support these exact IDs: `punctuation:no_comma`, `detectable_content:postscript`,
`startend:quotation`, `detectable_format:json_format`,
`detectable_content:number_placeholders`, `detectable_format:number_bullet_lists`,
`detectable_format:number_highlighted_sections`, `detectable_format:multiple_sections`,
`detectable_format:title`, and `startend:end_checker`.

```python
RuleChecker = Callable[[str, Mapping[str, object]], bool]


class IFEvalStrictEvaluator(Evaluator):
    """按固定 IFEval 官方规则逐项校验，并以全规则通过作为题目得分。"""

    metric_name = "ifeval_prompt_strict"

    def evaluate(
        self,
        prediction: str,
        reference: str,
        *,
        input_text: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MetricResult:
        instruction_ids, kwargs = _validated_rules(metadata)
        checks = [_RULES[item](prediction, arguments) for item, arguments in zip(
            instruction_ids, kwargs, strict=True
        )]
        return MetricResult(
            metric=self.metric_name,
            score=1.0 if prediction.strip() and all(checks) else 0.0,
            reason=None if checks and all(checks) else "one or more IFEval rules failed",
            metadata={"checks": dict(zip(instruction_ids, checks, strict=True))},
        )
```

Copy rule behavior from the pinned Google Research revision, retain Apache-2.0 attribution in the
module header, and use only standard-library `json` and `re` operations. These ten rules were chosen
because their official checkers do not require `langdetect`, NLTK, or another new dependency. Reject
unsupported IDs before model execution when loading the manifest.

- [ ] **Step 4: Normalize selected IFEval rows**

Load `key`, `prompt`, `instruction_id_list`, and `kwargs`; use the integer key as `source_key`; set the
sample reference to an empty string because scoring is rule-based; place the official rule lists in
metadata. Confirm the ten exact keys in Global Constraints each contain one matching supported rule.

- [ ] **Step 5: Register and run evaluator tests**

Run: `.venv/bin/python -m pytest tests/test_ifeval_evaluator.py tests/test_hexagon_text_samples.py tests/test_runner.py -q`

Expected: PASS.

- [ ] **Step 6: Commit official-rule scoring**

```bash
git add src/evalhub/evaluators src/evalhub/datasets/hexagon_sources.py tests/test_ifeval_evaluator.py tests/test_hexagon_text_samples.py
git commit -m "feat: score selected ifeval rules locally"
```

---

### Task 5: Run HumanEval Pass@1 Only Inside Docker

**Files:**
- Create: `docker/hexagon-humaneval/Dockerfile`
- Create: `docker/hexagon-humaneval/verify.py`
- Create: `scripts/build_humaneval_image.sh`
- Create: `src/evalhub/benchmarks/humaneval.py`
- Create: `src/evalhub/benchmarks/readiness.py`
- Create: `tests/test_humaneval_sandbox.py`
- Create: `tests/integration/test_humaneval_docker.py`
- Modify: `src/evalhub/benchmarks/__init__.py`
- Modify: `src/evalhub/datasets/hexagon_sources.py`

**Interfaces:**
- Consumes: pinned HumanEval gzip and ten selected task IDs from Tasks 2–3.
- Produces: `HumanEvalProblem`, `DockerHumanEvalSandbox`, `run_humaneval_benchmark(...)`, and `benchmark_readiness(spec) -> ExecutorReadiness`.

- [ ] **Step 1: Write failing command-safety and result tests**

```python
def test_docker_command_has_no_network_or_host_mount() -> None:
    command = DockerHumanEvalSandbox().command()

    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--pids-limit=64" in command
    assert not any(item in {"-v", "--volume", "--mount"} for item in command)


def test_humaneval_runner_reports_pass_at_one_without_exposing_tests(tmp_path: Path) -> None:
    sandbox = FakeSandbox(passed=True)
    result = run_humaneval_benchmark(
        job_id="job_1",
        adapter=StaticMappingAdapter({"prompt": "    return 1"}),
        problems=[problem_fixture(prompt="prompt")],
        sandbox=sandbox,
    )

    assert result["metric"] == "pass@1"
    assert result["passed_samples"] == 1
    assert "hidden_test" not in json.dumps(result)
```

- [ ] **Step 2: Run tests and confirm the sandbox boundary is missing**

Run: `.venv/bin/python -m pytest tests/test_humaneval_sandbox.py -q`

Expected: FAIL importing `DockerHumanEvalSandbox`.

- [ ] **Step 3: Add the fixed verifier image**

```dockerfile
FROM python:3.11-slim
RUN useradd --create-home --uid 10001 runner
COPY verify.py /opt/evalhub/verify.py
RUN chmod 0444 /opt/evalhub/verify.py
USER 10001:10001
ENTRYPOINT ["python", "/opt/evalhub/verify.py"]
```

`verify.py` reads one JSON payload from stdin containing `prompt`, `completion`, `test`, and `entry_point`; installs a three-second `signal.alarm`; executes the candidate plus hidden tests; calls `check(candidate)`; and writes only `{"passed": true}` or `{"passed": false, "reason": "..."}`. It must never echo source code, test code, environment variables, or a traceback.

- [ ] **Step 4: Implement the host Docker boundary**

```python
class DockerHumanEvalSandbox:
    """通过无网络、无宿主挂载的固定 Docker 镜像验证一个 HumanEval 候选。"""

    image = "evalhub-humaneval:1.0.0"

    def command(self) -> list[str]:
        return [
            "docker", "run", "--rm", "-i",
            "--network=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--memory=256m", "--cpus=1", "--pids-limit=64",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            self.image,
        ]

    def run(self, problem: HumanEvalProblem, completion: str) -> SandboxResult:
        completed = subprocess.run(
            self.command(),
            input=_sandbox_payload(problem, completion),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        return _parse_sandbox_result(completed)
```

Readiness must require both `docker version` success and `docker image inspect evalhub-humaneval:1.0.0` success. `scripts/build_humaneval_image.sh` uses strict shell flags and builds only `docker/hexagon-humaneval` with that tag.

- [ ] **Step 5: Implement HumanEval source loading and Pass@1 execution**

Read the gzip without extracting it. Keep canonical solution and hidden tests only in `HumanEvalProblem`; emitted sample results contain prompt, completion, score, source key, and Chinese translation but use `reference="hidden tests passed"`.

```python
@dataclass(frozen=True)
class HumanEvalProblem:
    sample_id: str
    source_key: str
    prompt: str
    canonical_solution: str
    test: str
    entry_point: str
    input_zh: str


def run_humaneval_benchmark(
    *,
    job_id: str,
    adapter: ModelAdapter,
    problems: list[HumanEvalProblem],
    sandbox: HumanEvalSandbox,
    skip_sample_ids: frozenset[str] = frozenset(),
    on_progress: ProgressCallback | None = None,
    on_sample_result: SampleDictCallback | None = None,
) -> dict[str, object]:
    """为每题生成一次代码并在 Docker 中评分，返回 Pass@1 兼容摘要。"""
```

- [ ] **Step 6: Add unit and explicit Docker integration checks**

Unit tests inject fake subprocess/sandbox boundaries. The integration test is marked `integration` and skips unless `EVALHUB_RUN_DOCKER_TESTS=1`; when enabled it verifies one canonical solution passes and one incorrect solution fails.

Run unit: `.venv/bin/python -m pytest tests/test_humaneval_sandbox.py -q`

Run integration after building: `EVALHUB_RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_humaneval_docker.py -q`

Expected: both PASS when Docker is available and the image has been built.

- [ ] **Step 7: Commit the sandboxed code evaluator**

```bash
git add docker/hexagon-humaneval scripts/build_humaneval_image.sh src/evalhub/benchmarks src/evalhub/datasets/hexagon_sources.py tests/test_humaneval_sandbox.py tests/integration/test_humaneval_docker.py
git commit -m "feat: isolate hexagon humaneval scoring"
```

---

### Task 6: Preserve Provenance Through Runner and Persistent Workflow

**Files:**
- Modify: `src/evalhub/domain/entities.py`
- Modify: `src/evalhub/engine/runner.py`
- Modify: `src/evalhub/cli.py`
- Modify: `src/evalhub/tasks/executor.py`
- Modify: `src/evalhub/tasks/runtime.py`
- Modify: `src/evalhub/tasks/performance.py`
- Modify: `src/evalhub/tasks/workflow.py`
- Modify: `tests/test_runner.py`
- Modify: `tests/test_workflow_runtime.py`
- Modify: `tests/test_model_performance.py`

**Interfaces:**
- Consumes: text samples, IFEval evaluator, HumanEval runner, and shared readiness from Tasks 3–5.
- Produces: bilingual `EvaluationSampleResult.metadata`, seven executable workflow nodes, revision-rich capability output, and full-suite-only performance comparisons.

- [ ] **Step 1: Write failing metadata and workflow tests**

```python
def test_runner_preserves_display_metadata_without_sending_it_to_adapter() -> None:
    sample = EvaluationSample(
        id="s1",
        input="English only",
        reference="A",
        metadata={"input_zh": "仅供展示", "source_key": "subject:1"},
    )
    adapter = RecordingAdapter("A")

    results, _ = runner_for(adapter).run(job=job(), benchmark=benchmark(), samples=[sample])

    assert adapter.inputs == ["English only"]
    assert results[0].metadata == sample.metadata


def test_hexagon_workflow_has_seven_ready_benchmark_nodes() -> None:
    graph = build_workflow(request(suite_id="evalhub-hexagon-v1"))
    benchmark_nodes = [item for item in graph if item.kind == "benchmark"]

    assert len(benchmark_nodes) == 7
    assert sum(int(item.input["expected_sample_count"]) for item in benchmark_nodes) == 60
```

- [ ] **Step 2: Run focused tests and verify provenance is currently lost**

Run: `.venv/bin/python -m pytest tests/test_runner.py tests/test_workflow_runtime.py tests/test_model_performance.py -q`

Expected: FAIL because `EvaluationSampleResult` has no metadata and Hexagon is not executable.

- [ ] **Step 3: Add backward-compatible sample-result metadata**

```python
@dataclass(frozen=True)
class EvaluationSampleResult:
    job_id: str
    sample_id: str
    input: str
    prediction: str
    reference: str
    metric: str
    score: float
    reason: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("result"))
    created_at: datetime = field(default_factory=utc_now)
```

Set `metadata=sample.metadata` in `EvaluationRunner`; copy metadata into child-process sample events, SQLite checkpoint input/result, failed examples, and node sample responses. Keep old persisted rows compatible through the default empty mapping.

- [ ] **Step 4: Dispatch text and HumanEval executions**

In `_evaluation_process`, route only `request.dataset == "hexagon-humaneval"` to `run_humaneval_benchmark`; all other model datasets continue through `run_real_benchmark`. Build the same Ollama/oracle adapter boundary in both paths. Never add a HumanEval branch inside `EvaluationRunner`.

- [ ] **Step 5: Use shared readiness in asset preparation**

Inject `readiness_checker: Callable[[BenchmarkSpec], ExecutorReadiness]` into `PersistentWorkflowExecutor`. Native Hexagon sources become ready after checksum preparation; `hexagon-humaneval` becomes ready only when both data and Docker image are ready; existing LM Eval and other sandbox specs remain unavailable.

The workflow node input must include `expected_sample_count`, `dataset_revision`, `prompt_template_version`, and immutable `generation_config`. The final result must contain a `reproducibility` object with suite version, manifest SHA-256, source revisions, prompt versions, and generation config.

- [ ] **Step 6: Keep incomplete suites out of model performance**

```python
comparable = [
    task
    for task in tasks
    if task.status == "success"
    and task.request.evaluation_type == "model"
    and task.average_score is not None
]
```

Add a regression test proving a partial Hexagon result with a numeric `average_score` does not enter scopes or rankings.

- [ ] **Step 7: Run runtime and compatibility tests**

Run: `.venv/bin/python -m pytest tests/test_runner.py tests/test_workflow_runtime.py tests/test_model_performance.py tests/test_task_executor.py tests/test_task_repository.py -q`

Expected: PASS; Oracle/fake Hexagon execution persists 60 metadata-rich results and produces six 100-point dimensions.

- [ ] **Step 8: Commit persistent execution integration**

```bash
git add src/evalhub/domain src/evalhub/engine src/evalhub/cli.py src/evalhub/tasks tests/test_runner.py tests/test_workflow_runtime.py tests/test_model_performance.py tests/test_task_executor.py tests/test_task_repository.py
git commit -m "feat: execute reproducible hexagon workflow"
```

---

### Task 7: Expose Readiness and Bilingual Sample Details

**Files:**
- Modify: `src/evalhub/server.py`
- Modify: `src/evalhub/tasks/presentation.py`
- Modify: `tests/test_task_api.py`
- Modify: `tests/test_server_frontend.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/assets.ts`
- Modify: `frontend/src/lib/assets.test.ts`
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationNodeInspector.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationNodeInspector.test.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationResultDetail.tsx`
- Modify: `frontend/src/components/dashboard/EvaluationResultDetail.test.tsx`

**Interfaces:**
- Consumes: readiness, reproducibility, translations, and source metadata from Task 6.
- Produces: truthful `/api/benchmarks` and `/api/suites` readiness plus an English/Chinese sample-detail UI.

- [ ] **Step 1: Write failing API readiness and sample-detail tests**

```python
def test_hexagon_suite_api_reports_sixty_samples_and_dynamic_docker_readiness() -> None:
    status, response = call_handler(method="GET", path="/api/suites", service=service())
    suite = next(item for item in response["suites"] if item["id"] == "evalhub-hexagon-v1")

    assert status == 200
    assert suite["expected_sample_count"] == 60
    assert suite["benchmark_count"] == 7
    assert suite["capabilities"] == [item.value for item in Capability]


def test_sample_checkpoint_exposes_translation_and_source_metadata() -> None:
    response = sample_checkpoint(sample_fixture(
        result={"metadata": {"input_zh": "中文题目", "source_key": "task:1"}}
    ))

    assert response["result"]["metadata"]["input_zh"] == "中文题目"
```

Frontend test:

```tsx
expect(await screen.findByText("English prompt")).toBeInTheDocument();
expect(screen.getByText("中文辅助翻译")).toBeInTheDocument();
expect(screen.getByText("EvalHub 中文辅助翻译，非官方译文")).toBeInTheDocument();
expect(screen.getByText("HumanEval/7")).toBeInTheDocument();
```

- [ ] **Step 2: Run backend and frontend tests and verify the fields are absent**

Run backend: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_server_frontend.py -q`

Run frontend: `npm --prefix frontend test -- --run src/components/dashboard/EvaluationNodeInspector.test.tsx src/components/dashboard/EvaluationResultDetail.test.tsx src/lib/assets.test.ts`

Expected: FAIL on missing suite counts, readiness, and bilingual rendering.

- [ ] **Step 3: Make Registry API readiness truthful**

Use `benchmark_readiness()` for every Benchmark response instead of equating `ExecutorKind.NATIVE` with availability. Suite response fields must include `expected_sample_count`, six capability IDs, `ready_count`, and per-member readiness. Docker absence must produce `executor_not_ready` with the exact build command `./scripts/build_humaneval_image.sh`.

- [ ] **Step 4: Preserve typed bilingual metadata in the client**

```typescript
export interface EvaluationSampleMetadata {
  input_zh?: string;
  reference_zh?: string | null;
  source?: string;
  source_key?: string;
  source_revision?: string;
  translation_version?: string;
}
```

Keep checkpoint `result` backward compatible, but add a type guard in `frontend/src/lib/assets.ts` that returns `EvaluationSampleMetadata | null` only for valid string fields.

- [ ] **Step 5: Render all selected node samples with bilingual provenance**

Change the node inspector request from `status=failed` to no status filter. Rename the section from “失败样本” to “样本明细”, show the score/status, English input, Chinese translation, source and source key, and keep cursor pagination. Never place hidden HumanEval tests in the DOM or raw result JSON.

- [ ] **Step 6: Show suite-level reproducibility**

In `EvaluationResultDetail`, add a compact disclosure for suite version, manifest digest, source revisions, and prompt config. Keep the existing radar unchanged; its six scores already consume `capability_profile`.

- [ ] **Step 7: Run API, component, and build verification**

Run backend: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_server_frontend.py -q`

Run frontend: `npm --prefix frontend test -- --run`

Run build: `npm --prefix frontend run build`

Expected: PASS with no TypeScript or rendering errors.

- [ ] **Step 8: Commit API and UI integration**

```bash
git add src/evalhub/server.py src/evalhub/tasks/presentation.py tests/test_task_api.py tests/test_server_frontend.py frontend
git commit -m "feat: show bilingual hexagon provenance"
```

---

### Task 8: Document Setup and Verify the Complete Suite

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started/20260804_本地运行指南.md`
- Modify: `docs/architecture/20260804_API接口草案.md`
- Modify: `docs/architecture/20260804_数据模型.md`
- Modify: `docs/product/20260804_产品需求文档.md`
- Test: all backend and frontend tests

**Interfaces:**
- Consumes: all implementation tasks.
- Produces: runnable operator instructions and completion evidence.

- [ ] **Step 1: Document exact preparation and comparison commands**

Add these commands and explain that only English is scored:

```bash
.venv/bin/python run_evalhub.py prepare-dataset hexagon-mmlu
.venv/bin/python run_evalhub.py prepare-dataset hexagon-ifeval
.venv/bin/python run_evalhub.py prepare-dataset hexagon-gsm8k
.venv/bin/python run_evalhub.py prepare-dataset hexagon-bbh
.venv/bin/python run_evalhub.py prepare-dataset hexagon-humaneval
.venv/bin/python run_evalhub.py prepare-dataset hexagon-truthfulqa
.venv/bin/python run_evalhub.py prepare-dataset hexagon-bbq
./scripts/build_humaneval_image.sh
./scripts/start_local.sh
```

Document the seven official sources, fixed revision/digest contract, 60-call total, English-only scoring, display-only translation label, Docker requirement, partial-result semantics, and the fact that Mini Suite scores are not full upstream Benchmark scores.

- [ ] **Step 2: Update API and data-model documentation**

Document `expected_sample_count`, dynamic readiness, `EvaluationSampleResult.metadata`, `reproducibility`, source revision fields, and the node-sample bilingual metadata response. Preserve old response fields as optional-compatible additions.

- [ ] **Step 3: Run targeted backend tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_benchmark_registry.py \
  tests/test_hexagon_manifest.py \
  tests/test_hexagon_sources.py \
  tests/test_hexagon_text_samples.py \
  tests/test_ifeval_evaluator.py \
  tests/test_humaneval_sandbox.py \
  tests/test_workflow_runtime.py \
  tests/test_task_api.py -q
```

Expected: PASS.

- [ ] **Step 4: Run full repository checks**

Run:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
git diff --check
```

Expected: all PASS with no whitespace errors.

- [ ] **Step 5: Run frontend checks**

Run from the repository root:

```bash
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

Expected: PASS.

- [ ] **Step 6: Run explicit external integration checks**

With network and Docker available:

```bash
EVALHUB_RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_humaneval_docker.py -q
.venv/bin/python scripts/build_hexagon_manifest.py --root . --output /tmp/hexagon_v1_rebuilt.json
cmp src/evalhub/datasets/manifests/hexagon_v1.json /tmp/hexagon_v1_rebuilt.json
```

Expected: Docker canonical/incorrect cases PASS and the rebuilt manifest is byte-identical. If either external check cannot run, record the exact command, environment reason, and residual risk in the final handoff.

- [ ] **Step 7: Audit completion against the specification**

Confirm all of the following with authoritative output: Suite API reports 60 samples and seven sources; the manifest has six groups of 10; model adapter recordings contain English only; sample detail includes Chinese translations; HumanEval command has no network or mount; incomplete results are absent from model performance; old suites and tests remain unchanged.

- [ ] **Step 8: Commit documentation and final verification updates**

```bash
git add README.md docs
git commit -m "docs: operate professional hexagon benchmark"
```
