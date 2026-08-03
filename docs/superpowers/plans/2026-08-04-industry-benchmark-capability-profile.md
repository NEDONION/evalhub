# Industry Benchmark Capability Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible industry Benchmark registry, real local dataset execution, normalized six-dimension capability reports, and a Chinese radar-chart workbench.

**Architecture:** Keep EvalHub as the orchestration and reporting layer. Route each versioned Benchmark through a native, lm-eval, or sandboxed-code executor, preserve raw metrics, then aggregate normalized scores into six fixed capabilities. The existing standard-library HTTP server exposes the registry and report APIs, while the static frontend renders local-only assets.

**Tech Stack:** Python 3.11+, dataclasses, standard-library HTTP/JSON/CSV/ZIP tooling, optional `lm-evaluation-harness`, Docker for generated-code isolation, vanilla HTML/CSS/JavaScript, pytest/unittest.

## Global Constraints

- Single-Benchmark runs default to the complete test set; quick five-sample and custom limits require explicit selection.
- The six capability IDs are exactly `knowledge`, `instruction_following`, `mathematics`, `reasoning`, `coding`, and `safety_trust`.
- Version 1 uses objective local scoring only and does not use LLM-as-a-Judge.
- Raw Benchmark metrics remain the source of truth; normalized scores are only for cross-Benchmark presentation.
- Missing, unavailable, and failed Benchmarks are unassessed and never contribute zero to an aggregate.
- HumanEval and MBPP generated code never executes directly on the host.
- Ollama generation scores declare `protocol_scope=evalhub_generation` when they are not externally leaderboard-comparable.
- Frontend assets are served locally with no external CDN.

---

### Task 1: Versioned Benchmark and Suite Registry

**Files:**
- Create: `src/evalhub/benchmarks/__init__.py`
- Create: `src/evalhub/benchmarks/models.py`
- Create: `src/evalhub/benchmarks/registry.py`
- Test: `tests/test_benchmark_registry.py`

**Interfaces:**
- Consumes: no new internal interfaces.
- Produces: `Capability`, `ExecutorKind`, `NormalizationKind`, `BenchmarkSpec`, `BenchmarkSuiteSpec`, `benchmark_registry()`, `suite_registry()`, `get_benchmark_spec()`, and `get_suite_spec()`.

- [ ] **Step 1: Write the failing registry tests**

```python
from evalhub.benchmarks import Capability, benchmark_registry, suite_registry


def test_industry_core_suite_covers_all_six_capabilities() -> None:
    benchmarks = benchmark_registry()
    suite = suite_registry()["llm-industry-core-v1"]

    assert {benchmarks[item].capability for item in suite.benchmark_ids} == set(Capability)
    assert len(suite.benchmark_ids) >= 13


def test_every_benchmark_has_reproducibility_metadata() -> None:
    for spec in benchmark_registry().values():
        assert spec.version
        assert spec.dataset_source
        assert spec.dataset_revision
        assert spec.metric
        assert spec.weight > 0
```

- [ ] **Step 2: Run the tests and confirm the registry is missing**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_benchmark_registry.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'evalhub.benchmarks'`.

- [ ] **Step 3: Add the immutable registry models**

```python
from dataclasses import dataclass, field
from enum import StrEnum


class Capability(StrEnum):
    KNOWLEDGE = "knowledge"
    INSTRUCTION_FOLLOWING = "instruction_following"
    MATHEMATICS = "mathematics"
    REASONING = "reasoning"
    CODING = "coding"
    SAFETY_TRUST = "safety_trust"


class ExecutorKind(StrEnum):
    NATIVE = "native"
    LM_EVAL = "lm_eval"
    SANDBOXED_CODE = "sandboxed_code"


class NormalizationKind(StrEnum):
    SCALE_100 = "scale_100"
    CHANCE_CORRECTED = "chance_corrected"


@dataclass(frozen=True)
class BenchmarkSpec:
    id: str
    version: str
    display_name: str
    capability: Capability
    dataset_source: str
    dataset_revision: str
    homepage: str
    license: str
    expected_sample_count: int | None
    executor: ExecutorKind
    task_name: str
    metric: str
    normalization: NormalizationKind
    random_baseline: float | None = None
    weight: float = 1.0
    prompt_template_version: str = "evalhub-v1"
    few_shot: int = 0
    generation_config: dict[str, object] = field(
        default_factory=lambda: {"temperature": 0, "num_predict": 256}
    )
    requirements: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkSuiteSpec:
    id: str
    version: str
    display_name: str
    benchmark_ids: tuple[str, ...]
```

- [ ] **Step 4: Add the explicit core registry**

Implement `benchmark_registry()` with these exact mappings and source identifiers:

```python
CORE_ROWS = (
    ("mmlu-pro", "MMLU-Pro", Capability.KNOWLEDGE, "TIGER-Lab/MMLU-Pro", ExecutorKind.LM_EVAL, "mmlu_pro", "acc", 0.1),
    ("mmlu", "MMLU", Capability.KNOWLEDGE, "hendrycks/test", ExecutorKind.NATIVE, "mmlu", "acc", 0.25),
    ("ifeval", "IFEval", Capability.INSTRUCTION_FOLLOWING, "google/IFEval", ExecutorKind.LM_EVAL, "ifeval", "prompt_level_strict_acc", None),
    ("gsm8k", "GSM8K", Capability.MATHEMATICS, "openai/grade-school-math", ExecutorKind.NATIVE, "gsm8k", "exact_match", None),
    ("math-500", "MATH-500", Capability.MATHEMATICS, "HuggingFaceH4/MATH-500", ExecutorKind.LM_EVAL, "leaderboard_math_hard", "exact_match", None),
    ("bbh", "BIG-Bench Hard", Capability.REASONING, "google/BIG-bench", ExecutorKind.LM_EVAL, "bbh", "exact_match", None),
    ("arc-challenge", "ARC-Challenge", Capability.REASONING, "allenai/ai2_arc", ExecutorKind.NATIVE, "arc_challenge", "acc", 0.25),
    ("musr", "MuSR", Capability.REASONING, "TAUR-Lab/MuSR", ExecutorKind.LM_EVAL, "musr", "acc", 0.5),
    ("hellaswag", "HellaSwag", Capability.REASONING, "Rowan/hellaswag", ExecutorKind.NATIVE, "hellaswag", "acc", 0.25),
    ("humaneval", "HumanEval", Capability.CODING, "openai/human-eval", ExecutorKind.SANDBOXED_CODE, "humaneval", "pass@1", None),
    ("mbpp", "MBPP", Capability.CODING, "google-research-datasets/mbpp", ExecutorKind.SANDBOXED_CODE, "mbpp", "pass@1", None),
    ("truthfulqa", "TruthfulQA", Capability.SAFETY_TRUST, "sylinrl/TruthfulQA", ExecutorKind.NATIVE, "truthfulqa_mc1", "acc", 0.25),
    ("bbq", "BBQ", Capability.SAFETY_TRUST, "nyu-mll/BBQ", ExecutorKind.NATIVE, "bbq", "acc", 1 / 3),
)
```

Use `dataset_revision="upstream-v1+content-sha256"` until download time, then record the actual content SHA-256 in each run manifest. Set `NormalizationKind.CHANCE_CORRECTED` when the baseline is non-null and `SCALE_100` otherwise. Validate duplicate IDs, unknown suite members, and non-positive weights when the module builds the dictionaries.

- [ ] **Step 5: Run the registry tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_benchmark_registry.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the registry**

```bash
git add src/evalhub/benchmarks tests/test_benchmark_registry.py
git commit -m "feat: add versioned benchmark suite registry"
```

### Task 2: Raw Scores, Normalization, and Capability Aggregation

**Files:**
- Modify: `src/evalhub/benchmarks/models.py`
- Create: `src/evalhub/benchmarks/scoring.py`
- Modify: `src/evalhub/benchmarks/__init__.py`
- Test: `tests/test_capability_scoring.py`

**Interfaces:**
- Consumes: `Capability`, `BenchmarkSpec`, and `NormalizationKind` from Task 1.
- Produces: `BenchmarkScore`, `CapabilityScore`, `normalize_score(raw_score, spec)`, and `aggregate_capabilities(specs, results)`.

- [ ] **Step 1: Write failing normalization and coverage tests**

```python
def test_chance_corrected_score_clamps_below_baseline() -> None:
    spec = make_spec(baseline=0.25)
    assert normalize_score(0.25, spec) == 0.0
    assert normalize_score(0.625, spec) == 50.0
    assert normalize_score(1.0, spec) == 100.0


def test_partial_capability_ignores_missing_score_but_reports_coverage() -> None:
    scores = aggregate_capabilities(
        specs=[make_spec(id="a", weight=1), make_spec(id="b", weight=1)],
        results={"a": BenchmarkScore.success("a", raw_score=0.8)},
    )
    assert scores[Capability.KNOWLEDGE].score == 80.0
    assert scores[Capability.KNOWLEDGE].coverage == 0.5
    assert scores[Capability.KNOWLEDGE].status == "partial"
```

- [ ] **Step 2: Verify the tests fail for missing score types**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_capability_scoring.py -q`

Expected: FAIL importing `BenchmarkScore`.

- [ ] **Step 3: Implement deterministic score normalization**

```python
def normalize_score(raw_score: float, spec: BenchmarkSpec) -> float:
    if not 0.0 <= raw_score <= 1.0:
        raise ValueError(f"raw score outside [0, 1]: {raw_score}")
    if spec.normalization == NormalizationKind.SCALE_100:
        return round(raw_score * 100.0, 4)
    baseline = spec.random_baseline
    if baseline is None or not 0.0 <= baseline < 1.0:
        raise ValueError(f"invalid random baseline for {spec.id}: {baseline}")
    corrected = max(0.0, (raw_score - baseline) / (1.0 - baseline))
    return round(corrected * 100.0, 4)
```

Define `BenchmarkScore` with `benchmark_id`, `status`, `raw_score`, `normalized_score`, `metric`, `total_samples`, `passed_samples`, `protocol_scope`, and `error`. Define `CapabilityScore` with `capability`, nullable `score`, `status`, `coverage`, and `benchmark_results`.

```python
@dataclass(frozen=True)
class BenchmarkScore:
    benchmark_id: str
    status: str
    raw_score: float | None
    normalized_score: float | None
    metric: str
    total_samples: int
    passed_samples: int
    protocol_scope: str
    error: str | None = None

    @classmethod
    def success(cls, benchmark_id: str, raw_score: float) -> "BenchmarkScore":
        return cls(
            benchmark_id=benchmark_id,
            status="success",
            raw_score=raw_score,
            normalized_score=None,
            metric="acc",
            total_samples=1,
            passed_samples=int(raw_score >= 1.0),
            protocol_scope="evalhub_generation",
        )


@dataclass(frozen=True)
class CapabilityScore:
    capability: Capability
    score: float | None
    status: str
    coverage: float
    benchmark_results: tuple[BenchmarkScore, ...]
```

- [ ] **Step 4: Implement aggregation over successful weight only**

For each capability, calculate coverage as successful configured weight divided by total configured weight. Average successful normalized scores using only successful weight. Return `complete` at coverage `1.0`, `partial` above `0.0`, and `unassessed` with `score=None` at `0.0`.

```python
score = sum(item.normalized_score * spec.weight for spec, item in successful) / successful_weight
coverage = successful_weight / configured_weight
```

- [ ] **Step 5: Run focused and full tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_capability_scoring.py tests/test_runner.py -q`

Expected: PASS.

- [ ] **Step 6: Commit scoring**

```bash
git add src/evalhub/benchmarks tests/test_capability_scoring.py
git commit -m "feat: aggregate six-dimension capability scores"
```

### Task 3: Real Native Benchmark Datasets

**Files:**
- Modify: `src/evalhub/datasets/catalog.py`
- Modify: `src/evalhub/datasets/loaders.py`
- Modify: `src/evalhub/datasets/__init__.py`
- Create: `tests/fixtures/arc_challenge.jsonl`
- Create: `tests/fixtures/hellaswag.jsonl`
- Create: `tests/fixtures/truthfulqa_mc.json`
- Create: `tests/fixtures/bbq.jsonl`
- Create: `tests/test_native_benchmark_loaders.py`

**Interfaces:**
- Consumes: existing `EvaluationSample`, `_safe_extract()`, and dataset catalog APIs.
- Produces: preparation and loading support for `arc-challenge`, `hellaswag`, `truthfulqa`, and `bbq` in addition to GSM8K/MMLU.

- [ ] **Step 1: Add fixture-based failing loader tests**

Create minimal fixtures using the upstream field names and assert that every loader returns a prompt ending in `Answer:`, an uppercase letter reference, and metadata containing the upstream category or sample ID.

```json
{"id":"arc-1","question":{"stem":"Which option is correct?","choices":[{"label":"A","text":"one"},{"label":"B","text":"two"}]},"answerKey":"B"}
{"ind":"hs-1","ctx":"A person starts a task.","endings":["They finish it.","The moon turns green.","Time reverses.","Nothing exists."],"label":"0"}
{"context":"Context text","question":"Who is described?","ans0":"A","ans1":"B","ans2":"Unknown","label":2,"category":"Age"}
```

The TruthfulQA fixture is:

```json
{"Question":{"0":"What is true?"},"mc1_targets":{"0":{"choices":["Correct","Wrong"],"labels":[1,0]}}}
```

```python
def test_arc_loader_preserves_answer_key(tmp_path: Path) -> None:
    install_fixture(tmp_path, "arc-challenge", "ARC-Challenge-Test.jsonl")
    samples = load_samples("arc-challenge", root=tmp_path)
    assert samples[0].reference == "B"
    assert "A." in samples[0].input
    assert samples[0].metadata["dataset"] == "arc-challenge"
```

- [ ] **Step 2: Run and verify unknown-dataset failures**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_native_benchmark_loaders.py -q`

Expected: FAIL with `unknown dataset: arc-challenge`.

- [ ] **Step 3: Add official download entries**

Use these exact sources and local cache targets:

```python
NATIVE_SOURCES = {
    "arc-challenge": ("https://ai2-public-datasets.s3.amazonaws.com/arc/ARC-V1-Feb2018-2.zip", "data/raw/arc/ARC-Challenge-Test.jsonl"),
    "hellaswag": ("https://raw.githubusercontent.com/rowanz/hellaswag/master/data/hellaswag_val.jsonl", "data/raw/hellaswag/hellaswag_val.jsonl"),
    "truthfulqa": ("https://raw.githubusercontent.com/sylinrl/TruthfulQA/main/data/mc_task.json", "data/raw/truthfulqa/mc_task.json"),
    "bbq": ("https://github.com/nyu-mll/BBQ/archive/refs/heads/main.zip", "data/raw/bbq/BBQ-main/data"),
}
```

Download archives to `.download` files, verify they open successfully, then atomically rename into the cache. Reuse safe path validation for ZIP extraction and compute SHA-256 for every prepared artifact.

- [ ] **Step 4: Implement the four parsers**

- ARC reads JSONL `question.stem`, `question.choices[].label/text`, and `answerKey`.
- HellaSwag reads `ctx`, four `endings`, and integer-string `label`, mapping `0..3` to `A..D`.
- TruthfulQA reads `mc1_targets.choices` and the single `1` label from `mc1_targets.labels`.
- BBQ reads `context`, `question`, `ans0..ans2`, integer `label`, and category metadata.

All prompts use the shared helper:

```python
def _choice_prompt(question: str, choices: list[tuple[str, str]], *, context: str = "") -> str:
    body = "\n".join(f"{label}. {text}" for label, text in choices)
    prefix = f"Context: {context}\n" if context else ""
    return f"Answer the multiple-choice question. Return only the option letter.\n\n{prefix}Question: {question}\n{body}\n\nAnswer:"
```

- [ ] **Step 5: Run loader and regression tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_native_benchmark_loaders.py tests/test_cli_parser.py -q`

Expected: PASS.

- [ ] **Step 6: Commit native datasets**

```bash
git add src/evalhub/datasets tests/fixtures tests/test_native_benchmark_loaders.py
git commit -m "feat: add real native industry benchmarks"
```

### Task 4: Executor Contracts, lm-eval Bridge, and Suite Service

**Files:**
- Create: `src/evalhub/benchmarks/executors/__init__.py`
- Create: `src/evalhub/benchmarks/executors/base.py`
- Create: `src/evalhub/benchmarks/executors/native.py`
- Create: `src/evalhub/benchmarks/executors/lm_eval.py`
- Create: `src/evalhub/benchmarks/service.py`
- Modify: `src/evalhub/cli.py:103-181`
- Modify: `pyproject.toml`
- Test: `tests/test_lm_eval_executor.py`
- Test: `tests/test_benchmark_suite_service.py`

**Interfaces:**
- Consumes: Benchmark registry, dataset loaders, scoring APIs, Ollama adapter, and existing `EvaluationRunner`.
- Produces: `BenchmarkRunRequest`, `BenchmarkRunResult`, `BenchmarkExecutor`, `ExecutorRegistry`, `SuiteEvaluationService.run(request)`, and a serializable `SuiteRunResult`.

- [ ] **Step 1: Write failing executor command and partial-suite tests**

```python
def test_lm_eval_command_targets_ollama_openai_endpoint(tmp_path: Path) -> None:
    command = build_lm_eval_command(request(), output_dir=tmp_path)
    assert command[:2] == ["lm-eval", "run"]
    assert "local-chat-completions" in command
    assert "http://127.0.0.1:11434/v1/chat/completions" in " ".join(command)


def test_suite_keeps_success_when_an_executor_is_not_ready() -> None:
    result = service_with_one_success_one_unready().run(core_request())
    assert result.status == "partial"
    assert result.benchmark_results["gsm8k"].status == "success"
    assert result.benchmark_results["ifeval"].status == "executor_not_ready"
```

- [ ] **Step 2: Run tests and confirm the service is absent**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_lm_eval_executor.py tests/test_benchmark_suite_service.py -q`

Expected: FAIL importing executor modules.

- [ ] **Step 3: Define the executor request and result contracts**

```python
@dataclass(frozen=True)
class BenchmarkRunRequest:
    benchmark: BenchmarkSpec
    model: str
    adapter: str
    base_url: str
    limit: int | None = None
    runtime_config: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutorReadiness:
    ready: bool
    code: str
    message: str


@dataclass(frozen=True)
class BenchmarkRunResult:
    benchmark_id: str
    status: str
    metric: str
    raw_score: float | None
    total_samples: int
    passed_samples: int
    protocol_scope: str
    duration_seconds: float
    sample_results: tuple[dict[str, object], ...] = ()
    error: str | None = None


class BenchmarkExecutor(Protocol):
    def readiness(self, request: BenchmarkRunRequest) -> ExecutorReadiness: ...
    def run(self, request: BenchmarkRunRequest) -> BenchmarkRunResult: ...
```

Move the current `run_real_benchmark` behavior behind `NativeExecutor` and keep the CLI wrapper calling the new service so existing commands remain compatible.

- [ ] **Step 4: Add the optional lm-eval dependency and safe subprocess bridge**

Add:

```toml
[project.optional-dependencies]
benchmarks = ["lm-eval>=0.4.9,<0.5"]
```

Build argv without `shell=True`, normalize the Ollama base URL to `/v1/chat/completions`, create a temporary output directory, set `--log_samples`, pass `--limit` only when non-null, and parse the generated `results*.json`. Capture stdout/stderr with a timeout and return structured `executor_not_ready`, `timeout`, or `failed` results instead of raising across the suite boundary.

```python
def build_lm_eval_command(request: BenchmarkRunRequest, output_dir: Path) -> list[str]:
    endpoint = request.base_url.rstrip("/") + "/v1/chat/completions"
    command = [
        "lm-eval", "run", "--model", "local-chat-completions",
        "--model_args", f"model={request.model},base_url={endpoint}",
        "--tasks", request.benchmark.task_name,
        "--output_path", str(output_dir), "--log_samples",
    ]
    if request.limit is not None:
        command.extend(["--limit", str(request.limit)])
    return command
```

- [ ] **Step 5: Implement sequential suite orchestration**

`SuiteEvaluationService.run()` resolves either one Benchmark or a suite, executes each item, persists a manifest-ready result dictionary, calls `aggregate_capabilities()`, and returns `success` only when every item succeeds, `partial` when at least one succeeds, and `failed` otherwise. Default `limit=None` must flow unchanged through every executor.

```python
for spec in selected_specs:
    executor = self.executors.get(spec.executor)
    run_result = executor.run(replace(request, benchmark=spec))
    results[spec.id] = to_benchmark_score(spec, run_result)
capabilities = aggregate_capabilities(selected_specs, results)
successful = sum(item.status == "success" for item in results.values())
status = "success" if successful == len(results) else "partial" if successful else "failed"
```

- [ ] **Step 6: Run focused and full backend tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_lm_eval_executor.py tests/test_benchmark_suite_service.py tests/test_runner.py tests/test_cli_parser.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the mixed executor service**

```bash
git add pyproject.toml src/evalhub/benchmarks src/evalhub/cli.py tests/test_lm_eval_executor.py tests/test_benchmark_suite_service.py
git commit -m "feat: run mixed benchmark suites"
```

### Task 5: Docker-Isolated HumanEval and MBPP

**Files:**
- Create: `docker/eval-code/Dockerfile`
- Create: `docker/eval-code/run_case.py`
- Create: `src/evalhub/benchmarks/executors/sandboxed_code.py`
- Modify: `src/evalhub/datasets/catalog.py`
- Modify: `src/evalhub/datasets/loaders.py`
- Modify: `scripts/start_local.sh`
- Test: `tests/test_sandboxed_code_executor.py`

**Interfaces:**
- Consumes: `BenchmarkRunRequest`, Ollama generation, and official HumanEval/MBPP samples.
- Produces: `DockerCodeJudge.readiness()`, `DockerCodeJudge.evaluate(case)`, and `SandboxedCodeExecutor.run(request)`.

- [ ] **Step 1: Write failing Docker security command tests**

```python
def test_docker_code_command_is_networkless_and_read_only(tmp_path: Path) -> None:
    command = build_docker_case_command(tmp_path / "case.json")
    joined = " ".join(command)
    assert "--network none" in joined
    assert "--read-only" in command
    assert "--cap-drop ALL" in joined
    assert "--pids-limit 64" in joined
    assert "--memory 512m" in joined
```

- [ ] **Step 2: Verify the test fails for the missing executor**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_sandboxed_code_executor.py -q`

Expected: FAIL importing `sandboxed_code`.

- [ ] **Step 3: Add official code dataset preparation**

Use HumanEval `https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz` and sanitized MBPP `https://raw.githubusercontent.com/google-research/google-research/master/mbpp/sanitized-mbpp.json`. Load prompts and hidden tests into `EvaluationSample.metadata`; never place hidden tests in model prompts.

```python
CODE_SOURCES = {
    "humaneval": "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz",
    "mbpp": "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/sanitized-mbpp.json",
}
```

- [ ] **Step 4: Implement generation outside and judging inside Docker**

The host asks Ollama for a completion, extracts the first fenced Python block or raw response, writes a JSON case with generated code and hidden tests to a mode-`0444` temporary directory, and launches:

```python
[
    "docker", "run", "--rm", "--network", "none", "--read-only",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
    "--pids-limit", "64", "--memory", "512m", "--cpus", "1",
    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
    "-v", f"{case_dir}:/work:ro", "evalhub-code-runner:py311",
    "python", "/runner/run_case.py", "/work/case.json",
]
```

`run_case.py` enforces a per-case timeout, returns JSON only, and exits nonzero on test failure. The executor reports unassessed `executor_not_ready` when Docker or the image is unavailable.

- [ ] **Step 5: Add startup readiness detection without automatic image downloads**

`start_local.sh` checks `docker info` and `docker image inspect evalhub-code-runner:py311`; it prints the exact build command when missing but does not silently download/build a large image.

```bash
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker image inspect evalhub-code-runner:py311 >/dev/null 2>&1 || \
    echo "代码评测镜像未构建：docker build -t evalhub-code-runner:py311 docker/eval-code"
fi
```

- [ ] **Step 6: Run security contract and regression tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_sandboxed_code_executor.py tests/test_benchmark_suite_service.py -q`

Expected: PASS without requiring Docker because command construction and not-ready behavior are injected.

- [ ] **Step 7: Commit isolated code evaluation**

```bash
git add docker/eval-code src/evalhub/benchmarks/executors/sandboxed_code.py src/evalhub/datasets scripts/start_local.sh tests/test_sandboxed_code_executor.py
git commit -m "feat: isolate code benchmark execution"
```

### Task 6: Benchmark, Suite, and Report HTTP APIs

**Files:**
- Modify: `src/evalhub/server.py:18-98`
- Create: `src/evalhub/report_store.py`
- Test: `tests/test_benchmark_api.py`

**Interfaces:**
- Consumes: Registry, dataset status, `SuiteEvaluationService`, and capability result types.
- Produces: `/api/benchmarks`, `/api/suites`, expanded `/api/datasets/prepare`, suite-aware `/api/evaluations/run`, and `/api/evaluations/{job_id}/report`.

- [ ] **Step 1: Write failing API integration tests against an ephemeral server**

```python
def test_suites_endpoint_exposes_six_capabilities(client) -> None:
    body = client.get_json("/api/suites")
    core = next(item for item in body["suites"] if item["id"] == "llm-industry-core-v1")
    assert set(core["capabilities"]) == {item.value for item in Capability}


def test_suite_run_defaults_to_all_samples(client, fake_service) -> None:
    client.post_json("/api/evaluations/run", {"suite_id": "llm-industry-core-v1"})
    assert fake_service.last_request.limit is None
```

- [ ] **Step 2: Run and verify endpoint failures**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_benchmark_api.py -q`

Expected: FAIL with HTTP 404 for `/api/suites`.

- [ ] **Step 3: Add atomic JSON report persistence**

Store reports as `reports/<job_id>/report.json` using a temporary sibling file and `Path.replace()`. `ReportStore.get(job_id)` rejects path separators and returns `FileNotFoundError` for unknown IDs.

```python
def save(self, job_id: str, payload: dict[str, object]) -> Path:
    directory = self.root / _validate_job_id(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "report.json"
    temporary = directory / "report.json.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target
```

- [ ] **Step 4: Add registry and run routes**

Serialize enums as strings, include `prepared` and executor readiness, accept exactly one of `benchmark_id` or `suite_id`, and change `_parse_limit()` default mode from `custom` to `all`. Return HTTP 400 for invalid requests, 409 for blocked prerequisites, and 500 only for unexpected server failures.

- [ ] **Step 5: Run API and full Python tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: PASS.

- [ ] **Step 6: Commit the APIs**

```bash
git add src/evalhub/server.py src/evalhub/report_store.py tests/test_benchmark_api.py
git commit -m "feat: expose benchmark suites and capability reports"
```

### Task 7: Chinese Capability Workbench and Radar Chart

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/styles.css`
- Modify: `frontend/app.js`
- Create: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `/api/benchmarks`, `/api/suites`, and suite report JSON.
- Produces: suite/single segmented selection, readiness table, fixed six-axis local SVG radar, coverage labels, and Benchmark details table.

- [ ] **Step 0: Invoke the frontend design skill**

Read and apply `frontend-design` before editing the UI. Preserve the approved restrained blue/white enterprise direction, dense operational layout, 8px maximum card radius, and Chinese-first copy.

- [ ] **Step 1: Write failing static frontend contract tests**

```python
def test_workbench_contains_suite_and_radar_mounts() -> None:
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'id="suiteSelect"' in html
    assert 'id="capabilityRadar"' in html
    assert 'id="benchmarkResults"' in html


def test_frontend_defines_all_six_chinese_capability_labels() -> None:
    script = Path("frontend/app.js").read_text(encoding="utf-8")
    for label in ("知识", "指令遵循", "数学", "综合推理", "代码", "安全可信"):
        assert label in script
```

- [ ] **Step 2: Run and verify missing mounts**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frontend_contract.py -q`

Expected: FAIL because the radar mount is absent.

- [ ] **Step 3: Add suite selection and readiness presentation**

Replace the single dataset select with a segmented mode control (`suite`/`benchmark`), a dynamic suite select, and a dynamic single Benchmark select. Show total configured samples, prepared count, executor readiness, six-dimension coverage, and an explicit full-run warning. Keep `sample_mode=all` checked by default.

```javascript
const evaluationMode = document.querySelector("#evaluationMode");
const suiteSelect = document.querySelector("#suiteSelect");
const benchmarkSelect = document.querySelector("#benchmarkSelect");

function selectedEvaluationTarget() {
  return evaluationMode.value === "suite"
    ? { suite_id: suiteSelect.value }
    : { benchmark_id: benchmarkSelect.value };
}
```

- [ ] **Step 4: Render a fixed-dimension local SVG radar**

Implement `renderCapabilityRadar(capabilities)` with `document.createElementNS`. Use a stable `viewBox="0 0 520 360"`, five hexagonal grid levels, six labels, and a blue score polygon. Omit unassessed vertices from the score polygon and render `未评测` beside their labels; never substitute zero. Pair the chart with a six-row numeric table containing score, coverage, and status.

```javascript
const CAPABILITIES = [
  ["knowledge", "知识"],
  ["instruction_following", "指令遵循"],
  ["mathematics", "数学"],
  ["reasoning", "综合推理"],
  ["coding", "代码"],
  ["safety_trust", "安全可信"],
];

function radarPoint(index, score, cx = 260, cy = 180, radius = 128) {
  const angle = -Math.PI / 2 + index * Math.PI / 3;
  const scaled = radius * score / 100;
  return `${cx + Math.cos(angle) * scaled},${cy + Math.sin(angle) * scaled}`;
}
```

- [ ] **Step 5: Render Benchmark result details**

Create a dense table with Benchmark, capability, raw score, normalized score, protocol, samples, duration, and status. Add an expandable failure panel below the table. Keep panels at 8px radius or less and preserve current blue/white enterprise tokens.

```javascript
function benchmarkResultCells(result) {
  return [
    result.display_name,
    capabilityLabels[result.capability],
    formatScore(result.raw_score),
    formatScore(result.normalized_score),
    result.protocol_scope,
    `${result.passed_samples}/${result.total_samples}`,
    formatDuration(result.duration_seconds),
    benchmarkStatusLabels[result.status] || result.status,
  ];
}
```

- [ ] **Step 6: Run frontend syntax, contract, and Python tests**

Run: `node --check frontend/app.js`

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frontend_contract.py -q`

Expected: both PASS.

- [ ] **Step 7: Commit the workbench**

```bash
git add frontend tests/test_frontend_contract.py
git commit -m "feat: add six-dimension capability workbench"
```

### Task 8: Documentation and End-to-End Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started/LOCAL_RUN.md`
- Modify: `docs/architecture/API.md`
- Modify: `docs/architecture/DATA_MODEL.md`
- Modify: `docs/architecture/ARCHITECTURE.md`

**Interfaces:**
- Consumes: all completed implementation tasks.
- Produces: operator instructions and verified local startup behavior.

- [ ] **Step 1: Update operator documentation with exact commands**

Document base installation, optional Benchmark installation, full-suite data preparation, Docker image build, single/full/quick runs, report locations, protocol comparability, and the meaning of partial/unassessed capability scores.

```bash
.venv/bin/python -m pip install -e ".[dev,benchmarks]"
docker build -t evalhub-code-runner:py311 docker/eval-code
./scripts/start_local.sh
```

- [ ] **Step 2: Run the complete automated verification**

Run: `PYTHONPYCACHEPREFIX=/tmp/evalhub-pyc PYTHONPATH=src .venv/bin/python -m pytest -q`

Run: `PYTHONPYCACHEPREFIX=/tmp/evalhub-pyc .venv/bin/python -m compileall -q src run_evalhub.py tests`

Run: `node --check frontend/app.js`

Expected: all commands exit 0.

- [ ] **Step 3: Start the local service and verify APIs**

Run: `./scripts/start_local.sh`

Verify: `curl -s http://127.0.0.1:8000/api/health`

Verify: `curl -s http://127.0.0.1:8000/api/suites`

Expected: health status `ok` and a core suite containing all six capabilities.

- [ ] **Step 4: Verify the browser at desktop and mobile sizes**

Invoke `browser:control-in-app-browser`, open `http://127.0.0.1:8000`, run an Oracle quick suite for deterministic output, and capture 1440x900 and 390x844 screenshots. Confirm the radar is nonblank, values match the numeric table, no labels overlap, and all missing capabilities say `未评测`.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/getting-started/LOCAL_RUN.md docs/architecture/API.md docs/architecture/DATA_MODEL.md docs/architecture/ARCHITECTURE.md
git commit -m "docs: document industry capability evaluation"
```
