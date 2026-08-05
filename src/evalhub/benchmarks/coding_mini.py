"""提供可离线验证的 Pi Coding Mini Agent Benchmark。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol

from evalhub.agent.pi import (
    AgentTraceEvent,
    PiAgentError,
    PiAgentRunner,
    PiRunResult,
    TraceCallback,
)

CAPABILITY_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("planning", "规划"),
    ("code_understanding", "代码理解"),
    ("implementation", "实现正确性"),
    ("tool_use", "工具使用"),
    ("verification", "验证能力"),
    ("robustness", "稳健性"),
)

ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class CodingAgentSample:
    """描述一个包含初始仓库、任务说明、隐藏校验和能力权重的编码样本。"""

    id: str
    difficulty: Literal["easy", "medium", "hard"]
    difficulty_reason: str
    instruction: str
    files: Mapping[str, str]
    verifier_code: str
    capability_weights: Mapping[str, float]


class AgentRunner(Protocol):
    """定义 Coding Mini 对固定 Agent 壳所需的最小接口。"""

    def version(self) -> str:
        """返回 Agent 壳版本，供最终结果审计。"""

    def run(
        self,
        *,
        instruction: str,
        model: str,
        base_url: str,
        workspace: Path,
        timeout_seconds: float,
        on_event: TraceCallback | None = None,
    ) -> PiRunResult:
        """在隔离工作区执行一个编码样本并返回运行元数据。"""


def coding_mini_samples() -> tuple[CodingAgentSample, ...]:
    """返回内置 Coding Mini 样本。

    六条三级难度样本共同覆盖六项能力。任务与初始文件可以交给 Agent，隐藏校验代码
    只在 Agent 退出后由 EvalHub 执行，不写入样本仓库。
    """
    return (
        # 简单题要求使用标准路径语义，同时识别前缀相似但已逃逸根目录的边界。
        CodingAgentSample(
            id="path_normalization",
            difficulty="easy",
            difficulty_reason="单文件路径边界与目录逃逸",
            instruction=(
                "Repair normalize_user_path so it returns a normalized POSIX path contained "
                "by the absolute root, or None when the value escapes that root. Absolute "
                "values already inside the root are valid, and an empty value denotes the "
                "root. Preserve the signature and verify representative boundaries."
            ),
            files={
                "paths.py": (
                    "import posixpath\n\n"
                    "def normalize_user_path(root, value):\n"
                    "    return posixpath.normpath(posixpath.join(root, value))\n"
                ),
                "test_public.py": (
                    "from paths import normalize_user_path\n\n"
                    "def test_normalizes_child_path():\n"
                    "    assert normalize_user_path('/srv/data', 'reports/../today.txt') "
                    "== '/srv/data/today.txt'\n"
                )
            },
            verifier_code=(
                "from paths import normalize_user_path\n"
                "assert normalize_user_path('/srv/data', 'reports/../today.txt') "
                "== '/srv/data/today.txt'\n"
                "assert normalize_user_path('/srv/data', '') == '/srv/data'\n"
                "assert normalize_user_path('/srv/data', '/srv/data/a.txt') "
                "== '/srv/data/a.txt'\n"
                "assert normalize_user_path('/srv/data', '../secret.txt') is None\n"
                "assert normalize_user_path('/srv/data', '/srv/data2/a.txt') is None\n"
            ),
            capability_weights={
                "planning": 0.4,
                "code_understanding": 0.4,
                "implementation": 0.2,
            },
        ),
        # 第二道简单题把存在性与真值分开，并要求所有输入映射保持不变。
        CodingAgentSample(
            id="config_precedence",
            difficulty="easy",
            difficulty_reason="固定优先级与输入不可变约束",
            instruction=(
                "Repair resolve_setting so a present key is selected in this exact order: "
                "arguments, environment, file values, then default. Empty strings are present "
                "values and none of the supplied mappings may be mutated. Preserve the public "
                "signature and verify the change."
            ),
            files={
                "config.py": (
                    "def resolve_setting(name, arguments, environment, file_values, default):\n"
                    "    for source in (file_values, environment, arguments):\n"
                    "        if name in source:\n"
                    "            return source.pop(name)\n"
                    "    return default\n"
                ),
                "test_public.py": (
                    "from config import resolve_setting\n\n"
                    "def test_arguments_win():\n"
                    "    assert resolve_setting('mode', {'mode': 'arg'}, {'mode': 'env'}, "
                    "{'mode': 'file'}, 'default') == 'arg'\n"
                ),
            },
            verifier_code=(
                "from config import resolve_setting\n"
                "arguments = {'mode': ''}\n"
                "environment = {'mode': 'env'}\n"
                "file_values = {'mode': 'file', 'other': 'kept'}\n"
                "snapshots = (arguments.copy(), environment.copy(), file_values.copy())\n"
                "assert resolve_setting('mode', arguments, environment, file_values, "
                "'default') == ''\n"
                "assert (arguments, environment, file_values) == snapshots\n"
                "assert resolve_setting('mode', {}, environment, file_values, 'default') "
                "== 'env'\n"
                "assert resolve_setting('mode', {}, {}, file_values, 'default') == 'file'\n"
                "assert resolve_setting('missing', {}, {}, {}, 'default') == 'default'\n"
            ),
            capability_weights={
                "tool_use": 0.3,
                "verification": 0.3,
                "robustness": 0.4,
            },
        ),
        # 分页题要求阅读客户端边界，在保持顺序的同时避免重复记录与重复游标死循环。
        CodingAgentSample(
            id="pagination_merge",
            difficulty="medium",
            difficulty_reason="跨模块分页、去重与循环游标检测",
            instruction=(
                "Repair collect_records so it follows pages from the supplied cursor until "
                "next_cursor is None, keeps the first record for each id in encounter order, "
                "and raises ValueError when a cursor repeats. Preserve the signature, do not "
                "mutate page data, and verify the behavior."
            ),
            files={
                "client.py": (
                    "def fetch_page(pages, cursor):\n"
                    "    return pages[cursor]\n"
                ),
                "pagination.py": (
                    "from client import fetch_page\n\n"
                    "def collect_records(pages, cursor='first'):\n"
                    "    records = []\n"
                    "    while cursor is not None:\n"
                    "        page = fetch_page(pages, cursor)\n"
                    "        records.extend(page['records'])\n"
                    "        cursor = page.get('next_cursor')\n"
                    "    return records\n"
                ),
                "test_public.py": (
                    "from pagination import collect_records\n\n"
                    "def test_collects_pages():\n"
                    "    pages = {'first': {'records': [{'id': 1}], "
                    "'next_cursor': 'last'}, 'last': {'records': [{'id': 2}], "
                    "'next_cursor': None}}\n"
                    "    assert collect_records(pages) == [{'id': 1}, {'id': 2}]\n"
                )
            },
            verifier_code=(
                "from copy import deepcopy\n"
                "from pagination import collect_records\n"
                "pages = {'first': {'records': [{'id': 1, 'v': 'first'}, {'id': 2}], "
                "'next_cursor': 'second'}, 'second': {'records': [{'id': 1, 'v': 'later'}, "
                "{'id': 3}], 'next_cursor': None}}\n"
                "snapshot = deepcopy(pages)\n"
                "assert collect_records(pages) == "
                "[{'id': 1, 'v': 'first'}, {'id': 2}, {'id': 3}]\n"
                "assert pages == snapshot\n"
                "loop = {'first': {'records': [], 'next_cursor': 'again'}, "
                "'again': {'records': [], 'next_cursor': 'first'}}\n"
                "try:\n"
                "    collect_records(loop)\n"
                "except ValueError:\n"
                "    pass\n"
                "else:\n"
                "    raise AssertionError('repeated cursor must fail')\n"
            ),
            capability_weights={
                "planning": 0.1,
                "code_understanding": 0.2,
                "implementation": 0.25,
                "tool_use": 0.2,
                "verification": 0.15,
                "robustness": 0.1,
            },
        ),
        # 缓存题以注入时钟冻结时间，使边界可重复且不依赖真实等待。
        CodingAgentSample(
            id="cache_expiry",
            difficulty="medium",
            difficulty_reason="注入时钟、TTL 边界与选择性清理",
            instruction=(
                "Repair Cache so put stores positive-TTL entries against the injected clock, "
                "get returns None and removes an entry at or after expiry, and purge_expired "
                "removes only expired entries and returns their count. Non-positive TTL must "
                "not remain cached. Preserve the public methods and verify boundary behavior."
            ),
            files={
                "cache.py": (
                    "class Cache:\n"
                    "    def __init__(self, clock):\n"
                    "        self.clock = clock\n"
                    "        self.entries = {}\n\n"
                    "    def put(self, key, value, ttl):\n"
                    "        self.entries[key] = (value, self.clock() + ttl)\n\n"
                    "    def get(self, key):\n"
                    "        entry = self.entries.get(key)\n"
                    "        return None if entry is None else entry[0]\n\n"
                    "    def purge_expired(self):\n"
                    "        removed = len(self.entries)\n"
                    "        self.entries.clear()\n"
                    "        return removed\n"
                ),
                "test_public.py": (
                    "from cache import Cache\n\n"
                    "def test_hit_before_expiry():\n"
                    "    now = [10]\n"
                    "    cache = Cache(lambda: now[0])\n"
                    "    cache.put('key', 'value', 5)\n"
                    "    assert cache.get('key') == 'value'\n"
                )
            },
            verifier_code=(
                "from cache import Cache\n"
                "now = [100.0]\n"
                "cache = Cache(lambda: now[0])\n"
                "cache.put('a', 1, 5)\n"
                "cache.put('b', 2, 10)\n"
                "assert cache.get('a') == 1\n"
                "now[0] = 105.0\n"
                "assert cache.get('a') is None and 'a' not in cache.entries\n"
                "assert cache.purge_expired() == 0 and cache.get('b') == 2\n"
                "now[0] = 110.0\n"
                "assert cache.purge_expired() == 1 and cache.get('b') is None\n"
                "cache.put('zero', 3, 0)\n"
                "cache.put('negative', 4, -1)\n"
                "assert cache.get('zero') is None and cache.get('negative') is None\n"
            ),
            capability_weights={
                "planning": 0.15,
                "code_understanding": 0.1,
                "implementation": 0.2,
                "tool_use": 0.15,
                "verification": 0.2,
                "robustness": 0.2,
            },
        ),
        # 幂等预订题跨库存和审计边界，同时要求重复请求累计后整体检查。
        CodingAgentSample(
            id="reservation_idempotency",
            difficulty="hard",
            difficulty_reason="跨模块原子性、重复累计与幂等键",
            instruction=(
                "Repair reserve so duplicate item quantities are cumulative and a new "
                "idempotency key succeeds only when every quantity is positive and all stock "
                "is sufficient. Success deducts atomically, marks the key, and writes one "
                "audit record. Reusing a successful key is a no-op success; rejection changes "
                "nothing. Preserve the signature and verify the behavior."
            ),
            files={
                "inventory.py": (
                    "def deduct(stock, totals):\n"
                    "    for item, quantity in totals.items():\n"
                    "        stock[item] -= quantity\n"
                ),
                "audit.py": (
                    "def record(audit_log, key, totals):\n"
                    "    audit_log.append({'key': key, 'totals': totals})\n"
                ),
                "reservations.py": (
                    "from audit import record\n"
                    "from inventory import deduct\n\n"
                    "def reserve(stock, requests, idempotency_key, audit_log, processed):\n"
                    "    totals = dict(requests)\n"
                    "    deduct(stock, totals)\n"
                    "    processed.add(idempotency_key)\n"
                    "    record(audit_log, idempotency_key, totals)\n"
                    "    return True\n"
                ),
                "test_public.py": (
                    "from reservations import reserve\n\n"
                    "def test_successful_reservation():\n"
                    "    stock = {'pen': 3}\n"
                    "    log, processed = [], set()\n"
                    "    assert reserve(stock, [('pen', 2)], 'r1', log, processed) is True\n"
                    "    assert stock == {'pen': 1}\n"
                ),
            },
            verifier_code=(
                "from reservations import reserve\n"
                "stock = {'pen': 4, 'book': 2}\n"
                "log, processed = [], set()\n"
                "assert reserve(stock, [('pen', 2), ('pen', 1), ('book', 2)], "
                "'r1', log, processed) is True\n"
                "assert stock == {'pen': 1, 'book': 0}\n"
                "assert log == [{'key': 'r1', 'totals': {'pen': 3, 'book': 2}}]\n"
                "assert processed == {'r1'}\n"
                "assert reserve(stock, [('pen', 1)], 'r1', log, processed) is True\n"
                "assert stock == {'pen': 1, 'book': 0} and len(log) == 1\n"
                "snapshot = stock.copy()\n"
                "assert reserve(stock, [('pen', 2)], 'r2', log, processed) is False\n"
                "assert reserve(stock, [('pen', 0)], 'r3', log, processed) is False\n"
                "assert stock == snapshot and processed == {'r1'} and len(log) == 1\n"
            ),
            capability_weights={
                "planning": 0.2,
                "code_understanding": 0.2,
                "implementation": 0.2,
                "tool_use": 0.1,
                "verification": 0.1,
                "robustness": 0.2,
            },
        ),
        # 异步题验证取消与异常路径都执行队列确认和资源关闭，且不吞掉原始控制流。
        CodingAgentSample(
            id="async_worker_cleanup",
            difficulty="hard",
            difficulty_reason="异步取消、异常传播与双重清理",
            instruction=(
                "Repair run_once so every dequeued item is acknowledged exactly once, an opened "
                "resource is always awaited closed, and the handler result, exception, or "
                "cancellation propagates to the caller. Opening the resource can also fail. "
                "Preserve the public signature and verify success and cleanup paths."
            ),
            files={
                "worker.py": (
                    "async def run_once(queue, open_resource, handle):\n"
                    "    item = await queue.get()\n"
                    "    resource = await open_resource()\n"
                    "    result = await handle(resource, item)\n"
                    "    queue.task_done()\n"
                    "    resource.aclose()\n"
                    "    return result\n"
                ),
                "test_public.py": (
                    "import asyncio\n"
                    "from worker import run_once\n\n"
                    "def test_success():\n"
                    "    async def scenario():\n"
                    "        queue = asyncio.Queue()\n"
                    "        await queue.put('item')\n"
                    "        class Resource:\n"
                    "            async def aclose(self):\n"
                    "                pass\n"
                    "        async def open_resource():\n"
                    "            return Resource()\n"
                    "        async def handle(resource, item):\n"
                    "            return item\n"
                    "        assert await run_once(queue, open_resource, handle) == 'item'\n"
                    "    asyncio.run(scenario())\n"
                ),
            },
            verifier_code=(
                "import asyncio\n"
                "from worker import run_once\n\n"
                "class Resource:\n"
                "    def __init__(self):\n"
                "        self.closed = False\n"
                "    async def aclose(self):\n"
                "        self.closed = True\n\n"
                "async def main():\n"
                "    queue = asyncio.Queue()\n"
                "    await queue.put('failure')\n"
                "    resource = Resource()\n"
                "    async def open_resource():\n"
                "        return resource\n"
                "    async def fail(resource, item):\n"
                "        raise ValueError(item)\n"
                "    try:\n"
                "        await run_once(queue, open_resource, fail)\n"
                "    except ValueError as exc:\n"
                "        assert str(exc) == 'failure'\n"
                "    else:\n"
                "        raise AssertionError('handler exception must propagate')\n"
                "    await asyncio.wait_for(queue.join(), 0.1)\n"
                "    assert resource.closed\n"
                "    cancel_queue = asyncio.Queue()\n"
                "    await cancel_queue.put('cancel')\n"
                "    cancel_resource = Resource()\n"
                "    started = asyncio.Event()\n"
                "    async def open_cancel_resource():\n"
                "        return cancel_resource\n"
                "    async def block(resource, item):\n"
                "        started.set()\n"
                "        await asyncio.Event().wait()\n"
                "    task = asyncio.create_task("
                "run_once(cancel_queue, open_cancel_resource, block))\n"
                "    await started.wait()\n"
                "    task.cancel()\n"
                "    try:\n"
                "        await task\n"
                "    except asyncio.CancelledError:\n"
                "        pass\n"
                "    else:\n"
                "        raise AssertionError('cancellation must propagate')\n"
                "    await asyncio.wait_for(cancel_queue.join(), 0.1)\n"
                "    assert cancel_resource.closed\n"
                "    open_queue = asyncio.Queue()\n"
                "    await open_queue.put('open')\n"
                "    async def broken_open():\n"
                "        raise RuntimeError('open failed')\n"
                "    try:\n"
                "        await run_once(open_queue, broken_open, block)\n"
                "    except RuntimeError:\n"
                "        pass\n"
                "    await asyncio.wait_for(open_queue.join(), 0.1)\n\n"
                "asyncio.run(main())\n"
            ),
            capability_weights={
                "planning": 0.15,
                "code_understanding": 0.2,
                "implementation": 0.2,
                "tool_use": 0.1,
                "verification": 0.15,
                "robustness": 0.2,
            },
        ),
    )


def run_pi_agent_benchmark(
    *,
    job_id: str,
    model: str,
    base_url: str,
    difficulty: str,
    adapter: str = "ollama",
    provider_id: str | None = None,
    api_key: str | None = None,
    on_progress: ProgressCallback | None = None,
    runner: AgentRunner | None = None,
    runtime_root: Path = Path(".runtime/agent-runs"),
    on_trace: TraceCallback | None = None,
) -> dict[str, object]:
    """运行 Coding Mini 并聚合六维 Agent 能力报告。

    参数：
        job_id: 任务中心生成的唯一标识，用作运行目录名。
        model: Pi 使用的本地标签或 DeepSeek 模型 ID。
        base_url: Ollama 或 DeepSeek 服务根地址。
        difficulty: 运行全部样本或指定简单、中等、困难单档。
        adapter: ``ollama`` 或已支持的 ``openai-compatible``。
        provider_id: API 模式使用的服务商标识。
        api_key: Worker 临时解析的 API Key，不进入任务结果。
        on_progress: 接收已完成样本数和总数的可选回调。
        runner: 可替换的 Agent 壳；默认创建真实 ``PiAgentRunner``。
        runtime_root: 所有 Agent 样本工作区的父目录。
        on_trace: 接收样本阶段和 Pi 外部动作的可选实时回调。

    返回：
        与普通评测公共字段兼容，并包含 Agent 元数据、样本结果和六维报告的字典。

    异常：
        ValueError: 任务标识或难度无效。
        RuntimeError: 无法创建 Git 工作区或执行隐藏 Verifier。
        PiAgentError: 无法探测 Pi CLI 版本。
    """
    selected_samples = _select_samples(difficulty)
    job_root = _job_root(runtime_root, job_id)
    active_runner = runner or PiAgentRunner(
        adapter=adapter,
        provider_id=provider_id,
        api_key=api_key,
    )
    cli_version = active_runner.version()

    # 先公布真实分母，页面在第一个 Agent 样本运行期间也能显示确定进度。
    total_samples = len(selected_samples)
    if on_progress is not None:
        on_progress(0, total_samples)
    protocol_preflight = _run_protocol_preflight(
        job_root=job_root,
        model=model,
        base_url=base_url,
        runner=active_runner,
        on_trace=on_trace,
    )
    sample_results: list[dict[str, object]] = []

    # 每个样本拥有独立初始提交；某个 Pi 失败不会阻断后续能力维度采样。
    for completed, sample in enumerate(selected_samples, start=1):
        workspace = _create_workspace(job_root, sample)
        sample_results.append(
            _run_sample(
                sample=sample,
                workspace=workspace,
                model=model,
                base_url=base_url,
                runner=active_runner,
                on_trace=on_trace,
                protocol_status=str(protocol_preflight["status"]),
            )
        )
        if on_progress is not None:
            on_progress(completed, total_samples)

    # 样本得分与能力得分都来自隐藏校验，不读取或相信 Agent 的最终自然语言声明。
    passed_samples = sum(result["status"] == "success" for result in sample_results)
    dimensions = _aggregate_dimensions(selected_samples, sample_results)
    overall_score = round(
        sum(float(item["score"]) for item in dimensions) / len(dimensions), 4
    )
    failed_ids = [
        str(result["sample_id"])
        for result in sample_results
        if result["status"] != "success"
    ]

    return {
        "job_id": job_id,
        "status": "success",
        "evaluation_type": "agent",
        "dataset": "coding_mini",
        "benchmark": "EvalHub Coding Mini",
        "benchmark_version": "coding-mini-v3",
        "requested_difficulty": difficulty,
        "model": model,
        "adapter": adapter,
        "metric": "hidden_verifier_pass_rate",
        "total_samples": total_samples,
        "passed_samples": passed_samples,
        "average_score": round(passed_samples / total_samples, 4),
        "failed_sample_ids": failed_ids,
        "failed_examples": _failed_examples(sample_results),
        "difficulty_report": _aggregate_difficulty(selected_samples, sample_results),
        "agent": {
            "framework": "pi",
            "cli_version": cli_version,
            "scaffold_hash": _scaffold_hash(selected_samples),
        },
        "protocol_preflight": protocol_preflight,
        "execution_summary": _aggregate_execution(sample_results),
        "capability_report": {
            "overall_score": overall_score,
            "dimensions": dimensions,
        },
        "sample_results": sample_results,
    }


def _run_protocol_preflight(
    *,
    job_root: Path,
    model: str,
    base_url: str,
    runner: AgentRunner,
    on_trace: TraceCallback | None,
) -> dict[str, object]:
    """用一次精确 marker 写入检查当前模型的 Pi 工具协议。

    参数：
        job_root: 当前任务独占的运行目录。
        model: 本次使用的基模名称。
        base_url: 已冻结的模型服务地址。
        runner: 与正式样本相同的固定 Agent 壳。
        on_trace: 接收预检外部动作的可选回调。

    返回：
        包含兼容状态、工具事实、耗时和 marker 证据的 JSON 兼容字典。
    """
    sample = CodingAgentSample(
        id="protocol_preflight",
        difficulty="easy",
        difficulty_reason="不计分的工具协议预检",
        instruction=(
            "Use one structured file tool to replace the complete contents of "
            "protocol_probe.txt with exactly OK followed by one newline. Then finish."
        ),
        files={"protocol_probe.txt": ""},
        verifier_code="",
        capability_weights={},
    )
    workspace = _create_workspace(job_root, sample)
    tool_call_count = 0
    tool_error_count = 0

    def relay_pi_event(event: AgentTraceEvent) -> None:
        """计数预检工具事件，并用固定预检标识转发审计事实。"""
        nonlocal tool_call_count, tool_error_count
        if event["event_type"] == "tool_started":
            tool_call_count += 1
        if event["event_type"] == "tool_finished" and event["payload"].get("is_error") is True:
            tool_error_count += 1
        payload = {**event["payload"], "sample_id": sample.id}
        _emit_trace(
            on_trace,
            event_type=event["event_type"],
            actor=event["actor"],
            message=event["message"],
            payload=payload,
        )

    # 预检错误是模型协议证据，不应阻断正式样本和把整个任务误报为基础设施失败。
    started_at = monotonic()
    final_message_present = False
    runner_error: str | None = None
    try:
        run_result = runner.run(
            instruction=sample.instruction,
            model=model,
            base_url=base_url,
            workspace=workspace,
            timeout_seconds=60,
            on_event=relay_pi_event,
        )
        tool_call_count = max(tool_call_count, run_result.tool_call_count)
        final_message_present = bool(run_result.final_message)
    except PiAgentError as exc:
        if exc.error_type is not None:
            raise
        runner_error = str(exc)
    wall_time_seconds = round(monotonic() - started_at, 3)

    # marker 必须字节精确且来自至少一次结构化工具事件，纯文本伪调用不能获得兼容状态。
    marker_path = workspace / "protocol_probe.txt"
    marker_written = marker_path.read_bytes() == b"OK\n"
    if marker_written and tool_call_count > 0 and final_message_present:
        status = "compatible"
    elif marker_written and tool_call_count > 0:
        status = "degraded"
    else:
        status = "incompatible"
    result: dict[str, object] = {
        "status": status,
        "marker_written": marker_written,
        "tool_call_count": tool_call_count,
        "tool_error_count": tool_error_count,
        "wall_time_seconds": wall_time_seconds,
        "final_message_present": final_message_present,
    }
    if runner_error is not None:
        result["message"] = runner_error
    return result


def _select_samples(difficulty: str) -> tuple[CodingAgentSample, ...]:
    """按难度返回稳定样本组，并拒绝未知选择。

    Args:
        difficulty: 全部或单个难度标识。

    Returns:
        按内置固定顺序选择的非空样本元组。

    Raises:
        ValueError: 难度不在 all、easy、medium、hard 中。
    """
    if difficulty == "all":
        return coding_mini_samples()
    if difficulty not in {"easy", "medium", "hard"}:
        raise ValueError("difficulty must be one of: all, easy, medium, hard")
    return tuple(sample for sample in coding_mini_samples() if sample.difficulty == difficulty)


def _job_root(runtime_root: Path, job_id: str) -> Path:
    """解析任务运行目录并拒绝可能逃逸父目录的任务标识。"""
    if not job_id or Path(job_id).name != job_id or job_id in {".", ".."}:
        raise ValueError("job_id must be a safe path segment")
    job_root = runtime_root / job_id
    job_root.mkdir(parents=True, exist_ok=False)
    return job_root


def _create_workspace(job_root: Path, sample: CodingAgentSample) -> Path:
    """写入样本初始文件并创建本地 Git 基线提交。"""
    workspace = job_root / sample.id / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)

    # 样本文件名由内置定义控制，但仍验证相对路径以防未来编辑时引入目录逃逸。
    for relative_name, content in sample.files.items():
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RuntimeError(f"unsafe sample file path: {relative_name}")
        destination = workspace / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    # 初始提交让 Pi 可以用标准 Git 工具观察自己的变更，身份只对本次提交生效。
    _run_setup_command(["git", "init", "--quiet"], workspace)
    _run_setup_command(["git", "add", "."], workspace)
    _run_setup_command(
        [
            "git",
            "-c",
            "user.name=EvalHub",
            "-c",
            "user.email=evalhub@localhost",
            "commit",
            "--quiet",
            "-m",
            "Initial benchmark fixture",
        ],
        workspace,
    )
    return workspace


def _run_setup_command(command: list[str], workspace: Path) -> None:
    """执行 Git 初始化命令，并把环境错误转换为可诊断的任务级异常。"""
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"failed to initialize benchmark workspace: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise RuntimeError(f"failed to initialize benchmark workspace: {detail[-1000:]}")


def _run_sample(
    *,
    sample: CodingAgentSample,
    workspace: Path,
    model: str,
    base_url: str,
    runner: AgentRunner,
    on_trace: TraceCallback | None,
    protocol_status: str = "compatible",
) -> dict[str, object]:
    """运行单条 Agent 样本并生成文件证据、隐藏校验和可解释分类。"""
    difficulty_label = {"easy": "简单", "medium": "中等", "hard": "困难"}[sample.difficulty]
    _emit_trace(
        on_trace,
        event_type="sample_started",
        actor="benchmark",
        message=f"[{difficulty_label}] {sample.instruction}",
        payload={
            "sample_id": sample.id,
            "instruction": sample.instruction,
            "difficulty": sample.difficulty,
            "difficulty_reason": sample.difficulty_reason,
        },
    )
    observed_tool_calls = 0
    observed_tool_errors = 0

    def relay_pi_event(event: AgentTraceEvent) -> None:
        """计数 Pi 工具事件，并补充稳定样本标识后向上游转发。"""
        nonlocal observed_tool_calls, observed_tool_errors
        if event["event_type"] == "tool_started":
            observed_tool_calls += 1
        if event["event_type"] == "tool_finished" and event["payload"].get("is_error") is True:
            observed_tool_errors += 1
        payload = {**event["payload"], "sample_id": sample.id}
        _emit_trace(
            on_trace,
            event_type=event["event_type"],
            actor=event["actor"],
            message=event["message"],
            payload=payload,
        )

    # 计时包住整个 Agent 边界，使超时、非零退出和缺少最终消息也能留下真实耗时。
    started_at = monotonic()
    try:
        run_result = runner.run(
            instruction=sample.instruction,
            model=model,
            base_url=base_url,
            workspace=workspace,
            timeout_seconds=180,
            on_event=relay_pi_event,
        )
    except PiAgentError as exc:
        if exc.error_type is not None:
            raise
        wall_time_seconds = round(monotonic() - started_at, 3)
        # Runner 错误不代表最终实现错误；评分仍必须由独立隐藏校验决定。
        changed_files = _changed_files(workspace)
        verifier_passed, verifier_message = _verify_workspace(sample, workspace)
        outcome = "passed" if verifier_passed else "runtime_error"
        diagnostics = _diagnostics(
            outcome=outcome,
            tool_call_count=observed_tool_calls,
            tool_error_count=observed_tool_errors,
            changed_files=changed_files,
            wall_time_seconds=wall_time_seconds,
            final_message_present=False,
            verifier_passed=verifier_passed,
        )

        # 审计同时保留 Runner 警告、文件证据和 Verifier 结论，避免把自述当评分门槛。
        _emit_runner_error(on_trace, sample.id, exc)
        _emit_workspace_changed(on_trace, sample.id, changed_files)
        _emit_verifier_finished(
            on_trace,
            sample_id=sample.id,
            verifier_passed=verifier_passed,
            verifier_message=verifier_message,
        )
        score = 1.0 if verifier_passed else 0.0
        _emit_sample_finished(on_trace, sample.id, diagnostics, score=score)
        return {
            "sample_id": sample.id,
            "difficulty": sample.difficulty,
            "difficulty_reason": sample.difficulty_reason,
            "status": "success" if verifier_passed else "failed",
            "score": score,
            "final_message": "",
            "event_count": 0,
            "wall_time_seconds": wall_time_seconds,
            "verifier_message": verifier_message,
            "diagnostics": diagnostics,
        }

    wall_time_seconds = round(monotonic() - started_at, 3)
    # Git 变化回答 Agent 是否真正采取动作，隐藏校验只回答最终实现是否正确。
    changed_files = _changed_files(workspace)
    _emit_workspace_changed(on_trace, sample.id, changed_files)
    verifier_passed, verifier_message = _verify_workspace(sample, workspace)
    _emit_verifier_finished(
        on_trace,
        sample_id=sample.id,
        verifier_passed=verifier_passed,
        verifier_message=verifier_message,
    )
    if verifier_passed:
        outcome = "passed"
    elif changed_files:
        outcome = "wrong_solution"
    elif protocol_status == "incompatible":
        outcome = "protocol_error"
    else:
        outcome = "no_action"
    diagnostics = _diagnostics(
        outcome=outcome,
        tool_call_count=max(observed_tool_calls, run_result.tool_call_count),
        tool_error_count=observed_tool_errors,
        changed_files=changed_files,
        wall_time_seconds=wall_time_seconds,
        final_message_present=bool(run_result.final_message),
        verifier_passed=verifier_passed,
    )
    score = 1.0 if verifier_passed else 0.0
    _emit_sample_finished(on_trace, sample.id, diagnostics, score=score)
    return {
        "sample_id": sample.id,
        "difficulty": sample.difficulty,
        "difficulty_reason": sample.difficulty_reason,
        "status": "success" if verifier_passed else "failed",
        "score": score,
        "final_message": run_result.final_message[:1000],
        "event_count": run_result.event_count,
        "wall_time_seconds": wall_time_seconds,
        "verifier_message": verifier_message,
        "diagnostics": diagnostics,
    }


def _changed_files(workspace: Path) -> list[str]:
    """返回相对初始提交发生变化的受控文件路径。

    已跟踪修改和新增文件分别由 Git 查询，诊断目录与 Python 字节码不会冒充 Agent 行为。
    """
    tracked = _run_git_names(["git", "diff", "--name-only", "-z", "HEAD"], workspace)
    untracked = _run_git_names(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        workspace,
    )
    candidates = {*tracked, *untracked}
    return sorted(path for path in candidates if path and _is_controlled_change(path))


def _run_git_names(command: list[str], workspace: Path) -> list[str]:
    """执行只读 Git 文件名查询并返回 NUL 分隔的相对路径。

    异常：
        RuntimeError: Git 无法启动、超时或返回非零退出码时抛出。
    """
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"failed to inspect benchmark workspace: {exc}") from exc

    # 文件证据不可用时必须让平台显式失败，不能把未知状态误报为 no_action。
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise RuntimeError(f"failed to inspect benchmark workspace: {detail[-1000:]}")
    return [item for item in completed.stdout.split("\0") if item]


def _is_controlled_change(relative_name: str) -> bool:
    """判断相对路径是否属于可用于证明 Agent 行为的受控文件。"""
    parts = Path(relative_name).parts
    if ".evalhub" in parts or "__pycache__" in parts:
        return False
    return Path(relative_name).suffix != ".pyc"


def _diagnostics(
    *,
    outcome: str,
    tool_call_count: int,
    tool_error_count: int,
    changed_files: list[str],
    wall_time_seconds: float,
    final_message_present: bool,
    verifier_passed: bool,
) -> dict[str, object]:
    """构造最终结果和实时事件共同使用的样本诊断事实。"""
    return {
        "outcome": outcome,
        "tool_call_count": tool_call_count,
        "tool_error_count": tool_error_count,
        "changed_files": changed_files,
        "wall_time_seconds": wall_time_seconds,
        "final_message_present": final_message_present,
        "verifier_passed": verifier_passed,
    }


def _emit_trace(
    callback: TraceCallback | None,
    *,
    event_type: str,
    actor: str,
    message: str | None,
    payload: dict[str, object],
) -> None:
    """向可选上游发送一条 JSON 兼容且不包含隐藏断言的审计事件。"""
    if callback is None:
        return
    event: AgentTraceEvent = {
        "event_type": event_type,
        "actor": actor,
        "message": message,
        "payload": payload,
    }
    callback(event)


def _emit_runner_error(
    callback: TraceCallback | None,
    sample_id: str,
    error: PiAgentError,
) -> None:
    """发送明确归属于运行边界的失败事件。"""
    _emit_trace(
        callback,
        event_type="runner_error",
        actor="benchmark",
        message=str(error),
        payload={
            "sample_id": sample_id,
            "error_type": "pi_agent_error",
            "message": str(error),
        },
    )


def _emit_workspace_changed(
    callback: TraceCallback | None,
    sample_id: str,
    changed_files: list[str],
) -> None:
    """发送 Pi 退出后由 Git 独立观察到的受控文件变化。"""
    message = f"修改 {len(changed_files)} 个受控文件" if changed_files else "无受控文件变化"
    _emit_trace(
        callback,
        event_type="workspace_changed",
        actor="benchmark",
        message=message,
        payload={"sample_id": sample_id, "changed_files": changed_files},
    )


def _emit_verifier_finished(
    callback: TraceCallback | None,
    *,
    sample_id: str,
    verifier_passed: bool,
    verifier_message: str,
) -> None:
    """在 Agent 已退出后发送隐藏校验结论和安全失败摘要。"""
    message = "隐藏校验通过" if verifier_passed else "隐藏校验失败"
    _emit_trace(
        callback,
        event_type="verifier_finished",
        actor="benchmark",
        message=message,
        payload={
            "sample_id": sample_id,
            "passed": verifier_passed,
            "message": verifier_message,
        },
    )


def _emit_sample_finished(
    callback: TraceCallback | None,
    sample_id: str,
    diagnostics: dict[str, object],
    *,
    score: float,
) -> None:
    """发送单条样本的最终可解释分类。"""
    outcome_labels = {
        "runtime_error": "Agent 运行失败",
        "no_action": "未产生代码修改",
        "wrong_solution": "修改未通过隐藏校验",
        "protocol_error": "工具协议不兼容",
        "passed": "样本通过",
    }
    outcome = str(diagnostics["outcome"])
    _emit_trace(
        callback,
        event_type="sample_finished",
        actor="benchmark",
        message=outcome_labels[outcome],
        payload={"sample_id": sample_id, "score": score, **diagnostics},
    )


def _verify_workspace(sample: CodingAgentSample, workspace: Path) -> tuple[bool, str]:
    """在 Agent 退出后执行未暴露给它的 Python 断言代码。"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", sample.verifier_code],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"hidden verifier could not execute for {sample.id}: {exc}") from exc

    # 非零退出代表实现不满足断言，是样本失败而不是评测平台故障。
    if completed.returncode == 0:
        return True, "hidden verifier passed"
    detail = completed.stderr.strip() or completed.stdout.strip() or "hidden verifier failed"
    return False, detail[-1000:]


def _aggregate_dimensions(
    samples: tuple[CodingAgentSample, ...], sample_results: list[dict[str, object]]
) -> list[dict[str, object]]:
    """按样本声明权重计算固定顺序的六维归一化分数。"""
    passed_by_id = {
        str(result["sample_id"]): result["status"] == "success" for result in sample_results
    }
    dimensions: list[dict[str, object]] = []

    # 分母只累计实际选中样本，保证未来按 limit 运行时报告仍可解释。
    for key, label in CAPABILITY_DIMENSIONS:
        total_weight = sum(float(sample.capability_weights.get(key, 0.0)) for sample in samples)
        passed_weight = sum(
            float(sample.capability_weights.get(key, 0.0))
            for sample in samples
            if passed_by_id[sample.id]
        )
        score = passed_weight / total_weight if total_weight else 0.0
        dimensions.append({"key": key, "label": label, "score": round(score, 4)})
    return dimensions


def _aggregate_difficulty(
    samples: tuple[CodingAgentSample, ...],
    sample_results: list[dict[str, object]],
) -> list[dict[str, object]]:
    """按实际选择的难度顺序汇总隐藏校验通过率。

    Args:
        samples: 本次运行的稳定样本集合。
        sample_results: 与样本对应的隐藏校验结果。

    Returns:
        仅包含已运行难度的通过数、总数和通过率列表。
    """
    passed_ids = {
        str(result["sample_id"])
        for result in sample_results
        if result["status"] == "success"
    }
    report: list[dict[str, object]] = []

    # 固定顺序让不同运行的 JSON 和前端展示无需额外排序即可直接比较。
    for difficulty in ("easy", "medium", "hard"):
        tier = [sample for sample in samples if sample.difficulty == difficulty]
        if not tier:
            continue
        passed = sum(sample.id in passed_ids for sample in tier)
        report.append(
            {
                "difficulty": difficulty,
                "total": len(tier),
                "passed": passed,
                "pass_rate": round(passed / len(tier), 4),
            }
        )
    return report


def _aggregate_execution(sample_results: list[dict[str, object]]) -> dict[str, object]:
    """把正式样本诊断聚合为任务级过程指标。

    参数：
        sample_results: 仅包含计分样本的结果，不包含协议预检。

    返回：
        工具、耗时、文件变化和固定结果分类的汇总字典。
    """
    diagnostics = [dict(result["diagnostics"]) for result in sample_results]
    total_samples = len(diagnostics)
    total_tool_calls = sum(int(item["tool_call_count"]) for item in diagnostics)
    total_tool_errors = sum(int(item["tool_error_count"]) for item in diagnostics)
    wall_times = [float(item["wall_time_seconds"]) for item in diagnostics]

    # 不跨工作区合并同名文件；每条样本的改动文件数直接求和才符合隔离语义。
    total_changed_files = sum(len(list(item["changed_files"])) for item in diagnostics)
    outcome_counts = {
        outcome: sum(item["outcome"] == outcome for item in diagnostics)
        for outcome in (
            "passed",
            "no_action",
            "wrong_solution",
            "runtime_error",
            "protocol_error",
        )
    }
    divisor = total_samples or 1
    total_wall_time = sum(wall_times)
    return {
        "total_tool_calls": total_tool_calls,
        "average_tool_calls": round(total_tool_calls / divisor, 2),
        "total_tool_errors": total_tool_errors,
        "total_wall_time_seconds": round(total_wall_time, 2),
        "average_wall_time_seconds": round(total_wall_time / divisor, 2),
        "max_wall_time_seconds": round(max(wall_times, default=0.0), 2),
        "total_changed_files": total_changed_files,
        "outcome_counts": outcome_counts,
    }


def _failed_examples(sample_results: list[dict[str, object]]) -> list[dict[str, object]]:
    """提取通用结果详情可直接展示的失败样例。

    Agent Benchmark 没有传统问答数据集的输入与参考答案，因此把样本标识作为输入、
    Agent 最终消息作为预测，并用隐藏校验通过作为期望结果；额外保留校验错误供诊断。
    """
    return [
        {
            "sample_id": result["sample_id"],
            "difficulty": result["difficulty"],
            "difficulty_reason": result["difficulty_reason"],
            "input": str(result["sample_id"]),
            "prediction": str(result["final_message"]),
            "reference": "hidden verifier passed",
            "score": result["score"],
            "reason": result["verifier_message"],
        }
        for result in sample_results
        if result["status"] != "success"
    ]


def _scaffold_hash(samples: tuple[CodingAgentSample, ...]) -> str:
    """计算样本定义哈希，标识生成报告所用的固定 Agent 评测脚手架。"""
    payload = [
        {
            "id": sample.id,
            "difficulty": sample.difficulty,
            "difficulty_reason": sample.difficulty_reason,
            "instruction": sample.instruction,
            "files": dict(sample.files),
            "verifier_code": sample.verifier_code,
            "capability_weights": dict(sample.capability_weights),
        }
        for sample in samples
    ]
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
