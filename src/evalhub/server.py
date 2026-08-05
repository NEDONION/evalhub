"""提供本地 React 静态资源与 EvalHub JSON API 的轻量多线程 HTTP 服务。"""

import ipaddress
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from evalhub.adapters import discover_models
from evalhub.benchmarks import (
    BenchmarkSpec,
    Capability,
    ExecutorKind,
    ExecutorReadiness,
    benchmark_readiness,
    benchmark_registry,
    get_benchmark_spec,
    get_suite_spec,
    suite_registry,
)
from evalhub.benchmarks.harness import prepare_harness_benchmark
from evalhub.cli import run_real_benchmark
from evalhub.datasets import dataset_catalog, load_samples, prepare_dataset
from evalhub.model_providers import (
    BUILTIN_PROVIDERS,
    ModelProvider,
    ModelProviderCredentialError,
    ModelProviderNotFoundError,
    ModelProviderRepository,
    default_model_provider_repository,
)
from evalhub.ollama import DEFAULT_OLLAMA_BASE_URL, DEFAULT_OLLAMA_MODEL, get_ollama_status
from evalhub.ollama_pull import OllamaPullManager
from evalhub.tasks import (
    EvaluationTaskService,
    EvaluationType,
    SQLiteTaskRepository,
    TaskConflictError,
    TaskNotFoundError,
    TaskRequest,
)
from evalhub.tasks.presentation import (
    model_performance_report,
    node_detail,
    node_summary,
    sample_page,
    task_detail,
    task_summary,
)

OLLAMA_PULL_MANAGER = OllamaPullManager()
CAPABILITY_LABELS = {
    "knowledge": "知识",
    "instruction_following": "指令遵循",
    "mathematics": "数学",
    "reasoning": "综合推理",
    "coding": "代码",
    "safety_trust": "安全可信",
}


def frontend_directory(project_root: Path) -> Path:
    """定位已构建的 React 静态资源目录并验证入口文件。

    Args:
        project_root: 同时包含 ``frontend`` 与 Python 源码的项目根目录。

    Returns:
        包含 ``index.html`` 的 Vite 构建产物目录。

    Raises:
        FileNotFoundError: 前端尚未执行生产构建。
    """
    # 服务只托管构建产物，避免把 Vite 源码误当作浏览器可直接运行的静态文件。
    directory = project_root / "frontend" / "dist"
    if not (directory / "index.html").is_file():
        raise FileNotFoundError(
            "React frontend build not found. Run: npm --prefix frontend run build"
        )
    return directory


class EvalHubRequestHandler(SimpleHTTPRequestHandler):
    """同时处理控制台静态文件、数据集、Ollama 和评测 API 请求。"""

    # 自定义服务器标识便于本地诊断时区分 Python 开发服务与其他静态服务器。
    server_version = "EvalHubLocal/0.1"
    task_service: EvaluationTaskService | None = None
    provider_repository: ModelProviderRepository | None = None

    def __init__(self, *args: object, directory: str | None = None, **kwargs: object) -> None:
        """初始化请求处理器并选择显式目录或默认前端构建目录。

        Args:
            *args: ``SimpleHTTPRequestHandler`` 所需的位置参数。
            directory: 测试可覆盖的静态资源目录。
            **kwargs: ``SimpleHTTPRequestHandler`` 所需的关键字参数。

        Raises:
            FileNotFoundError: 未指定目录且默认 React 构建产物不存在。
        """
        # 从包文件向上定位项目根目录，避免依赖启动命令的当前工作目录。
        root = Path(__file__).resolve().parents[2]
        static_directory = Path(directory) if directory else frontend_directory(root)
        # 始终把规范化后的字符串路径交给标准库处理器，保留其静态文件行为。
        super().__init__(*args, directory=str(static_directory), **kwargs)

    def do_GET(self) -> None:
        """路由健康、资产、模型成绩、评测任务和 React 静态资源请求。

        Side Effects:
            向当前 HTTP 连接写入 JSON 或静态文件响应；下载查询只读取管理器内存状态。
        """
        # 先解析路径与查询参数，API 路由不受静态文件路径重写影响。
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"status": "ok", "service": "evalhub"})
            return
        if parsed.path == "/api/model-providers":
            # 列表只序列化脱敏领域对象，因此允许控制台在未配置密钥时直接展示预设。
            providers = [
                _model_provider_payload(provider)
                for provider in self._require_provider_repository().list()
            ]
            self._json({"providers": providers})
            return
        # 数据集端点会动态计算本地准备状态，与静态健康响应保持职责分离。
        if parsed.path == "/api/datasets":
            self._json(self._dataset_status())
            return
        if parsed.path == "/api/benchmarks":
            self._json(_benchmark_catalog())
            return
        if parsed.path == "/api/suites":
            self._json(_suite_catalog())
            return
        if parsed.path == "/api/ollama/status":
            # 查询参数允许控制台切换模型与服务地址，同时保留安全默认值。
            query = parse_qs(parsed.query)
            model = _first(query, "model", DEFAULT_OLLAMA_MODEL)
            base_url = _first(query, "base_url", DEFAULT_OLLAMA_BASE_URL)
            self._json(get_ollama_status(model=model, base_url=base_url))
            return
        if parsed.path == "/api/ollama/pulls":
            # 下载状态必须指定模型，避免在未来多任务列表扩展前误返回其他模型信息。
            query = parse_qs(parsed.query)
            model = _first(query, "model", "").strip()
            if not model:
                self._json({"ok": False, "error": "model is required"}, status=400)
                return
            self._json({"ok": True, "task": OLLAMA_PULL_MANAGER.get(model)})
            return
        if parsed.path == "/api/model-performance":
            query = parse_qs(parsed.query)
            scope = _first(query, "scope", "").strip() or None
            evaluation_type_text = _first(query, "evaluation_type", "model").strip()
            if evaluation_type_text not in {"model", "agent"}:
                self._json(
                    {"ok": False, "error": "evaluation_type must be model or agent"},
                    status=400,
                )
                return
            evaluation_type: EvaluationType = (
                "agent" if evaluation_type_text == "agent" else "model"
            )
            try:
                report = self._require_task_service().model_performance(scope, evaluation_type)
            except ValueError as exc:
                # 未知范围属于可恢复筛选错误，不能回退后混合不同评测口径。
                self._json({"ok": False, "error": str(exc)}, status=400)
                return
            self._json(model_performance_report(report))
            return
        if parsed.path == "/api/evaluations":
            # 高频轮询只发送轻量摘要，完整结果由用户选择任务后再按需读取。
            tasks = [task_summary(task) for task in self._require_task_service().list()]
            self._json({"tasks": tasks})
            return
        sample_route = _node_samples_route(parsed.path)
        if sample_route is not None:
            task_id, node_id = sample_route
            query = parse_qs(parsed.query)
            try:
                limit = int(_first(query, "limit", "50"))
                status = _first(query, "status", "").strip() or None
                cursor = _first(query, "cursor", "").strip() or None
                page = self._require_task_service().list_node_samples(
                    task_id,
                    node_id,
                    limit=limit,
                    cursor=cursor,
                    status=status,
                )
            except TaskNotFoundError as exc:
                self._json({"ok": False, "error": _exception_message(exc)}, status=404)
                return
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, status=400)
                return
            self._json(sample_page(page))
            return
        node_route = _node_detail_route(parsed.path)
        if node_route is not None:
            task_id, node_id = node_route
            try:
                service = self._require_task_service()
                node = service.get_node(task_id, node_id)
                events = service.list_node_events(task_id, node_id)
            except TaskNotFoundError as exc:
                self._json({"ok": False, "error": _exception_message(exc)}, status=404)
                return
            self._json({"node": node_detail(node, events=events)})
            return
        task_id = _task_detail_id(parsed.path)
        if task_id is not None:
            try:
                service = self._require_task_service()
                task = service.get(task_id)
                nodes = service.list_nodes(task_id)
            except TaskNotFoundError as exc:
                self._json({"ok": False, "error": _exception_message(exc)}, status=404)
                return
            self._json({"task": task_detail(task, nodes=nodes)})
            return
        # 根路径映射到 Vite 构建入口，其他路径继续使用标准静态文件处理逻辑。
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        """处理模型下载、数据集准备和同步评测三个本地写操作 API。

        Side Effects:
            可能启动 Ollama 后台下载线程、更新本地数据集缓存或同步执行真实评测。
            所有入口异常都会在 HTTP 边界转换为结构化 JSON，不向浏览器发送 Python 堆栈。
        """
        # 路径先于正文解析，未知端点无需读取可能很大的请求负载。
        parsed = urlparse(self.path)
        if parsed.path == "/api/model-providers":
            if not self._require_loopback_client():
                return
            self._create_model_provider()
            return

        provider_test_id = _model_provider_test_id(parsed.path)
        if provider_test_id is not None:
            if not self._require_loopback_client():
                return
            self._test_model_provider(provider_test_id)
            return

        if parsed.path == "/api/ollama/pulls":
            payload = self._read_json()
            model = payload.get("model")
            base_url = payload.get("base_url", DEFAULT_OLLAMA_BASE_URL)
            if not isinstance(model, str) or not model.strip():
                self._json({"ok": False, "error": "model is required"}, status=400)
                return
            if not isinstance(base_url, str) or not base_url.strip():
                self._json({"ok": False, "error": "base_url must be a string"}, status=400)
                return
            # 输入格式错误属于客户端问题；后台线程或传输初始化错误属于本地服务问题。
            try:
                task = OLLAMA_PULL_MANAGER.start(model.strip(), base_url.strip())
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                # HTTP 入口需要捕获下载管理器边界的未知实现异常，保证连接返回可诊断响应。
                self._json({"ok": False, "error": str(exc)}, status=500)
            else:
                self._json({"ok": True, "task": task}, status=202)
            return

        if parsed.path == "/api/evaluations":
            try:
                request = _task_request(
                    self._read_json(),
                    provider_repository=self._require_provider_repository(optional=True),
                )
                task = self._require_task_service().submit(request)
            except (TypeError, ValueError) as exc:
                # 无效组合不得写入持久队列，浏览器可以直接展示字段级诊断。
                self._json({"ok": False, "error": str(exc)}, status=400)
                return
            self._json({"ok": True, "task": task_summary(task)}, status=202)
            return

        retry_route = _node_retry_route(parsed.path)
        if retry_route is not None:
            task_id, node_id = retry_route
            try:
                node = self._require_task_service().retry_node(task_id, node_id)
            except TaskNotFoundError as exc:
                self._json({"ok": False, "error": _exception_message(exc)}, status=404)
                return
            except TaskConflictError as exc:
                self._json({"ok": False, "error": str(exc)}, status=409)
                return
            self._json({"ok": True, "node": node_summary(node)}, status=202)
            return

        cancel_task_id = _cancel_task_id(parsed.path)
        if cancel_task_id is not None:
            try:
                task = self._require_task_service().cancel(cancel_task_id)
            except TaskNotFoundError as exc:
                self._json({"ok": False, "error": _exception_message(exc)}, status=404)
                return
            except TaskConflictError as exc:
                self._json({"ok": False, "error": str(exc)}, status=409)
                return
            self._json(
                {
                    "ok": True,
                    "task": task_detail(
                        task, nodes=self._require_task_service().list_nodes(task.id)
                    ),
                }
            )
            return

        if parsed.path == "/api/datasets/prepare":
            payload = self._read_json()
            dataset = str(payload.get("dataset", "gsm8k"))
            force = payload.get("force", False)
            if not isinstance(force, bool):
                self._json({"ok": False, "error": "force must be a boolean"}, status=400)
                return
            # 准备动作在成功前不创建评测任务；失败时保留已有的已验证缓存。
            try:
                spec = get_benchmark_spec(dataset)
                was_prepared = _dataset_is_prepared(dataset)
                if spec.executor == ExecutorKind.NATIVE:
                    path = prepare_dataset(dataset, force=force)
                    samples = load_samples(
                        dataset,
                        limit=100000,
                    )
                    sample_count = len(samples)
                elif dataset == "hexagon-humaneval":
                    path = prepare_dataset(dataset, force=force)
                    sample_count = spec.expected_sample_count
                else:
                    # Harness validate 负责官方任务和数据缓存，代码任务同时验证 Docker 边界。
                    path = prepare_harness_benchmark(dataset, force=force)
                    sample_count = spec.expected_sample_count
                operation = "updated" if force and was_prepared else "cached"
                self._json(
                    {
                        "ok": True,
                        "dataset": dataset,
                        "path": str(path),
                        "operation": operation,
                        "sample_count": sample_count,
                    }
                )
            except Exception as exc:
                # HTTP 边界把准备阶段的可诊断异常转换为 JSON，避免本地控制台连接中断。
                self._json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/evaluations/run":
            # 评测请求复用 CLI 业务入口，确保两种交互方式使用相同默认值和报告结构。
            payload = self._read_json()
            try:
                limit = _parse_limit(payload)
                result = run_real_benchmark(
                    dataset=str(payload.get("dataset", "gsm8k")),
                    adapter_type=str(payload.get("adapter", "ollama")),
                    model=str(payload.get("model", DEFAULT_OLLAMA_MODEL)),
                    base_url=str(payload.get("base_url", DEFAULT_OLLAMA_BASE_URL)),
                    limit=limit,
                    subject=str(payload.get("subject", "all")),
                )
                # 成功响应把报告放在 ``result`` 下，为前端保留统一的 ``ok`` 判定字段。
                self._json({"ok": True, "result": result})
            except Exception as exc:
                # 模型、数据或评测异常在本地 API 边界统一转换，正文保留原始诊断消息。
                self._json({"ok": False, "error": str(exc)}, status=500)
            return

        # 所有未注册 POST 路径返回结构化 404，避免落入静态文件处理器。
        self._json({"ok": False, "error": "not found"}, status=404)

    def do_PUT(self) -> None:
        """更新一个模型服务商的公开配置或加密凭据。

        Side Effects:
            仅回环客户端可以修改独立服务商数据库；留空 API Key 会保留旧密文。
        """
        provider_id = _model_provider_id(urlparse(self.path).path)
        if provider_id is None:
            self._json({"ok": False, "error": "not found"}, status=404)
            return
        if not self._require_loopback_client():
            return
        self._update_model_provider(provider_id)

    def do_DELETE(self) -> None:
        """删除模型服务商配置或取消仍在进行的本地模型下载任务。

        Side Effects:
            回环客户端可删除自定义服务商或重置内置项；下载路径继续取消 Ollama 线程。
        """
        parsed = urlparse(self.path)
        provider_id = _model_provider_id(parsed.path)
        if provider_id is not None:
            if not self._require_loopback_client():
                return
            self._delete_model_provider(provider_id)
            return
        if parsed.path != "/api/ollama/pulls":
            self._json({"ok": False, "error": "not found"}, status=404)
            return
        query = parse_qs(parsed.query)
        model = _first(query, "model", "").strip()
        if not model:
            self._json({"ok": False, "error": "model is required"}, status=400)
            return
        task = OLLAMA_PULL_MANAGER.cancel(model)
        if task is None:
            self._json({"ok": False, "error": "pull task not found"}, status=404)
            return
        self._json({"ok": True, "task": task})

    def _create_model_provider(self) -> None:
        """校验正文并创建一个带加密凭据的自定义服务商。

        Side Effects:
            写入脱敏配置和 Fernet 密文，响应中只返回公开服务商字段。
        """
        try:
            payload = self._read_json()
            name = _required_string(payload, "name")
            base_url = _required_string(payload, "base_url")
            api_key = _required_string(payload, "api_key")
            provider = self._require_provider_repository().save(
                None,
                name=name,
                base_url=base_url,
                api_key=api_key,
            )
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, status=400)
            return
        self._json({"ok": True, "provider": _model_provider_payload(provider)}, status=201)

    def _update_model_provider(self, provider_id: str) -> None:
        """更新已有服务商，未提供字段沿用当前公开值。

        Args:
            provider_id: 内置稳定 ID 或已存在的自定义 ID。

        Side Effects:
            保存地址、名称和可选新密钥；空密钥按仓储语义保留旧值。
        """
        repository = self._require_provider_repository()
        try:
            current = repository.get(provider_id)
            payload = self._read_json()
            name = _optional_string(payload, "name", current.name)
            base_url = _optional_string(payload, "base_url", current.base_url)
            api_key = _optional_string(payload, "api_key", None)
            provider = repository.save(
                provider_id,
                name=name,
                base_url=base_url,
                api_key=api_key,
            )
        except ModelProviderNotFoundError as exc:
            self._json({"ok": False, "error": _exception_message(exc)}, status=404)
            return
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, status=400)
            return
        self._json({"ok": True, "provider": _model_provider_payload(provider)})

    def _delete_model_provider(self, provider_id: str) -> None:
        """删除自定义服务商，或把内置服务商恢复成无凭据默认项。

        Args:
            provider_id: 请求删除或重置的服务商标识。

        Side Effects:
            从独立 SQLite 表删除对应记录，不修改任何历史评测任务。
        """
        try:
            self._require_provider_repository().delete(provider_id)
        except ModelProviderNotFoundError as exc:
            self._json({"ok": False, "error": _exception_message(exc)}, status=404)
            return
        self._json(
            {
                "ok": True,
                "provider_id": provider_id,
                "reset": provider_id in BUILTIN_PROVIDERS,
            }
        )

    def _test_model_provider(self, provider_id: str) -> None:
        """使用已保存凭据探测服务商模型列表。

        Args:
            provider_id: 已配置凭据的服务商标识。

        Side Effects:
            发起一次短超时 ``GET /models``，但不接收或返回浏览器传入的明文密钥。
        """
        repository = self._require_provider_repository()
        try:
            provider = repository.get(provider_id)
            api_key = repository.resolve_api_key(provider_id)
            models = discover_models(provider.base_url, api_key)
        except ModelProviderNotFoundError as exc:
            self._json({"ok": False, "error": _exception_message(exc)}, status=404)
            return
        except (ModelProviderCredentialError, ValueError) as exc:
            self._json({"ok": False, "error": str(exc)}, status=400)
            return
        except RuntimeError as exc:
            # 适配器错误已经执行密钥脱敏，502 明确表示配置已保存但上游探测失败。
            self._json({"ok": False, "error": str(exc)}, status=502)
            return
        self._json({"ok": True, "models": models})

    def _require_task_service(self) -> EvaluationTaskService:
        """返回处理器已经装配的任务服务。

        Returns:
            提供持久化列表、详情、提交与取消能力的任务服务。

        Raises:
            RuntimeError: 服务入口或测试没有为处理器装配任务服务。
        """
        if self.task_service is None:
            raise RuntimeError("evaluation task service is not configured")
        return self.task_service

    def _require_provider_repository(
        self,
        *,
        optional: bool = False,
    ) -> ModelProviderRepository | None:
        """返回处理器已经装配的模型服务商仓储。

        Args:
            optional: 兼容不使用 API 模型的旧测试和入口；为真时允许返回 ``None``。

        Returns:
            已配置的独立服务商仓储，或可选模式下的 ``None``。

        Raises:
            RuntimeError: 必需模式下服务入口没有装配仓储。
        """
        if self.provider_repository is None and not optional:
            raise RuntimeError("model provider repository is not configured")
        return self.provider_repository

    def _require_loopback_client(self) -> bool:
        """拒绝来自非回环客户端的凭据写入和凭据使用操作。

        Returns:
            客户端地址为回环 IP 时返回 ``True``；否则发送 403 并返回 ``False``。
        """
        try:
            is_loopback = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            is_loopback = False
        if is_loopback:
            return True
        # 即使服务被用户主动绑定公网地址，凭据管理边界也不能随监听地址一起放宽。
        self._json(
            {"ok": False, "error": "provider credentials are loopback-only"},
            status=403,
        )
        return False

    def _dataset_status(self) -> dict[str, object]:
        """汇总数据集元数据、本地准备状态和可读取样本数。

        Returns:
            含稳定目录顺序、公开来源、本地路径、缓存状态和样本数量的响应对象。
            损坏缓存仍保留 `prepared` 状态，但样本数返回 ``None`` 提示用户更新。
        """
        # Registry 顺序与 Suite、评测表单保持一致；原生目录只补充专属下载元数据。
        datasets = []
        native_catalog = dataset_catalog()
        for benchmark in benchmark_registry().values():
            native = native_catalog.get(benchmark.id)
            local_path = (
                native.local_path
                if native is not None
                else f".runtime/benchmarks/{benchmark.id}.json"
            )
            prepared = _dataset_is_prepared(benchmark.id)
            # 未准备的数据集不尝试加载；准备完成后尽可能计算控制台展示用样本数。
            sample_count = benchmark.expected_sample_count
            if prepared and native is not None:
                try:
                    # 当前公开测试集规模可控，上限用于防止异常缓存导致无界读取。
                    sample_count = len(
                        load_samples(
                            benchmark.id,
                            limit=100000,
                        )
                    )
                except Exception:
                    # 状态接口应继续展示损坏或不完整的数据集，由空样本数提示需要重新准备。
                    sample_count = None
            readiness = benchmark_readiness(benchmark)
            # 响应同时包含来源与本地信息，前端无需再拼接目录规格。
            datasets.append(
                {
                    "name": benchmark.id,
                    "display_name": benchmark.display_name,
                    "task_type": (
                        native.task_type if native is not None else benchmark.capability.value
                    ),
                    "evaluator_type": (
                        native.evaluator_type if native is not None else benchmark.metric
                    ),
                    "homepage": benchmark.homepage,
                    "source_url": (
                        native.source_url if native is not None else benchmark.dataset_source
                    ),
                    "local_path": local_path,
                    "description": (
                        native.description
                        if native is not None
                        else f"{benchmark.display_name} 官方公开 Benchmark。"
                    ),
                    "executor": benchmark.executor.value,
                    "capability": benchmark.capability.value,
                    "capability_label": CAPABILITY_LABELS[benchmark.capability.value],
                    "locally_runnable": readiness.ready,
                    "readiness_reason": None if readiness.ready else readiness.message,
                    "prepared": prepared,
                    "sample_count": sample_count,
                }
            )
        # 外层对象为后续分页或附加状态字段保留兼容扩展空间。
        return {"datasets": datasets}

    def _read_json(self) -> dict[str, object]:
        """按 ``Content-Length`` 读取请求体并解析为 JSON 对象。

        Returns:
            空正文对应空字典，否则返回解码后的 JSON 映射。

        Raises:
            ValueError: 长度头或 JSON 正文格式无效。
        """
        # 严格按长度读取，避免在持久连接中消费下一个请求的数据。
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _json(self, payload: dict[str, object], status: int = 200) -> None:
        """以 UTF-8 JSON 编码并发送完整 HTTP 响应。

        Args:
            payload: 需要序列化的 JSON 兼容响应对象。
            status: HTTP 状态码，默认表示成功。
        """
        # 保留中文并对领域枚举等对象使用字符串兜底，保证控制台响应可序列化。
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 标头结束后一次写入完整正文，长度与实际 UTF-8 字节数保持一致。
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """用统一 EvalHub 前缀输出标准库 HTTP 访问日志。"""
        # 保留客户端地址与标准库格式化内容，便于在终端追踪本地控制台请求。
        print(f"[evalhub] {self.address_string()} - {format % args}")


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """启动多线程本地 HTTP 服务并在中断时可靠释放套接字。

    Args:
        host: 监听地址，默认仅允许本机访问。
        port: 监听 TCP 端口。
    """
    # 任务数据库位于仓库忽略的运行目录，重启服务后仍可恢复历史与排队任务。
    project_root = Path(__file__).resolve().parents[2]
    task_repository = SQLiteTaskRepository(project_root / ".runtime" / "evalhub.db")
    task_service = EvaluationTaskService(task_repository)
    provider_repository = default_model_provider_repository()
    EvalHubRequestHandler.task_service = task_service
    EvalHubRequestHandler.provider_repository = provider_repository

    # 先绑定端口再启动 Worker，端口冲突时不会遗留不可访问的执行线程。
    server = ThreadingHTTPServer((host, port), EvalHubRequestHandler)
    try:
        task_service.start()
        print(f"EvalHub local console: http://{host}:{port}")
        print("Press Ctrl+C to stop.")
        # 主循环持续处理请求，终端中断被视为本地开发服务的正常停止信号。
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            # 用户主动中断属于正常本地退出流程，只输出提示而不传播堆栈。
            print("\nEvalHub local console stopped.")
    finally:
        # 先终止执行子进程，再关闭套接字，避免本地退出后继续占用 CPU 或 GPU。
        task_service.stop()
        server.server_close()


def _dataset_is_prepared(dataset: str) -> bool:
    """按目录规格判断数据集是否已有可更新的本地缓存。

    Args:
        dataset: 数据集目录中已注册的稳定名称。

    Returns:
        原生资产存在，或外部任务标记确认已真实加载数据时返回 ``True``。

    Raises:
        KeyError: 数据集名称未在公开目录注册。
    """
    benchmark = get_benchmark_spec(dataset)
    if benchmark.executor == ExecutorKind.NATIVE:
        path = Path(dataset_catalog()[dataset].local_path)
        return path.exists() and (path.is_file() or any(path.glob("*")))

    marker = Path(f".runtime/benchmarks/{dataset}.json")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("preparation") == "task_data_loaded"


def _benchmark_catalog() -> dict[str, object]:
    """构建行业 Benchmark Registry 及当前本地执行器真实就绪状态。

    Returns:
        包含稳定 Benchmark 元数据、本地可运行标记和不可用原因的 JSON 对象。
    """
    benchmarks = []
    # Registry 顺序即套件展示顺序；所有状态统一经共享就绪检查计算。
    for spec in benchmark_registry().values():
        benchmarks.append(_benchmark_payload(spec, CAPABILITY_LABELS))
    return {"benchmarks": benchmarks}


def _suite_catalog() -> dict[str, object]:
    """构建版本化评测套件并汇总当前可本地运行的成员数量。

    Returns:
        包含套件版本、成员 ID、总数和本地可运行数量的 JSON 对象。
    """
    benchmarks = benchmark_registry()
    suites = []
    # 每个套件成员均重用 Benchmark 目录的相同就绪语义，避免 Docker 状态产生漂移。
    for spec in suite_registry().values():
        members = [_suite_member_payload(benchmarks[item]) for item in spec.benchmark_ids]
        ready_count = sum(member["readiness"]["ready"] for member in members)
        expected_sample_count = sum(
            benchmarks[item].expected_sample_count or 0 for item in spec.benchmark_ids
        )
        # Capability 枚举定义显示顺序，安全可信的两个 Benchmark 不应重复占用一个维度。
        capabilities = [
            capability.value
            for capability in Capability
            if any(benchmarks[item].capability == capability for item in spec.benchmark_ids)
        ]
        # 保留旧计数字段，新增字段让新客户端无需根据成员状态自行推断。
        suites.append(
            {
                "id": spec.id,
                "version": spec.version,
                "display_name": spec.display_name,
                "benchmark_ids": list(spec.benchmark_ids),
                "benchmark_count": len(spec.benchmark_ids),
                "locally_runnable_count": ready_count,
                "expected_sample_count": expected_sample_count,
                "capabilities": capabilities,
                "ready_count": ready_count,
                "members": members,
            }
        )
    return {"suites": suites}


def _benchmark_payload(spec: BenchmarkSpec, capability_labels: dict[str, str]) -> dict[str, object]:
    """将 Benchmark 规格与共享执行器检查结果转换为兼容目录响应。

    Args:
        spec: Registry 返回的不可变 Benchmark 规格。
        capability_labels: 能力稳定标识对应的中文显示名称。

    Returns:
        同时包含旧版可运行字段和新版结构化就绪状态的安全 JSON 字典。
    """
    readiness = benchmark_readiness(spec)
    # 显式选择公开字段，避免内部生成配置或枚举表示意外改变 HTTP 契约。
    return {
        "id": spec.id,
        "version": spec.version,
        "display_name": spec.display_name,
        "capability": spec.capability.value,
        "capability_label": capability_labels[spec.capability.value],
        "dataset_source": spec.dataset_source,
        "dataset_revision": spec.dataset_revision,
        "homepage": spec.homepage,
        "executor": spec.executor.value,
        "metric": spec.metric,
        "expected_sample_count": spec.expected_sample_count,
        "locally_runnable": readiness.ready,
        "readiness_reason": None if readiness.ready else readiness.message,
        "readiness": _readiness_payload(readiness, spec.id),
    }


def _suite_member_payload(spec: BenchmarkSpec) -> dict[str, object]:
    """提供套件成员所需的最小身份、样本规模和执行器就绪信息。

    Args:
        spec: 已由套件引用验证过的 Benchmark 规格。

    Returns:
        不包含内部生成参数的成员响应，供客户端审计套件准备度。
    """
    readiness = benchmark_readiness(spec)
    return {
        "id": spec.id,
        "display_name": spec.display_name,
        "capability": spec.capability.value,
        "expected_sample_count": spec.expected_sample_count,
        "readiness": _readiness_payload(readiness, spec.id),
    }


def _readiness_payload(readiness: ExecutorReadiness, benchmark_id: str) -> dict[str, object]:
    """把共享就绪状态转为稳定 API 字段，并为 HumanEval 公开唯一构建指引。

    Args:
        readiness: 共享检查返回的就绪结果。
        benchmark_id: 当前 Benchmark 稳定标识，用于识别固定 HumanEval 构建命令。

    Returns:
        客户端可直接显示的状态码、说明和可选构建命令。
    """
    payload = {"ready": readiness.ready, "code": readiness.code, "message": readiness.message}
    # 镜像缺失和 Docker 不可用都只能通过这条受控脚本修复，不能推测其他命令。
    if benchmark_id == "hexagon-humaneval" and not readiness.ready:
        payload["build_command"] = "./scripts/build_humaneval_image.sh"
    return payload


def _first(query: dict[str, list[str]], key: str, default: str) -> str:
    """读取查询参数的第一个非空值，缺失时返回默认值。"""
    # ``parse_qs`` 为每个键返回列表，本地 API 只接受其中第一个值。
    values = query.get(key)
    if not values:
        return default
    return values[0] or default


def _required_string(payload: dict[str, object], key: str) -> str:
    """读取必填非空字符串字段并去除复制时产生的首尾空白。

    Args:
        payload: 已解析为对象的请求正文。
        key: 需要读取的稳定字段名。

    Returns:
        去除首尾空白后的非空字符串。

    Raises:
        ValueError: 字段缺失、类型错误或内容为空。
    """
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_string(
    payload: dict[str, object],
    key: str,
    default: str | None,
) -> str | None:
    """读取可选字符串字段，缺失时沿用调用方提供的当前值。

    Args:
        payload: 已解析为对象的请求正文。
        key: 允许更新的稳定字段名。
        default: 字段未出现时返回的当前值。

    Returns:
        用户提交的原字符串，或未提交时的默认值。

    Raises:
        ValueError: 字段存在但不是字符串。
    """
    if key not in payload:
        return default
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _model_provider_payload(provider: ModelProvider) -> dict[str, object]:
    """把脱敏服务商对象转换成稳定 JSON 字段。

    Args:
        provider: 不包含密文和明文凭据的公开服务商对象。

    Returns:
        含配置状态、末四位提示和 ISO 时间的响应字典。
    """
    return {
        "id": provider.id,
        "name": provider.name,
        "kind": provider.kind,
        "base_url": provider.base_url,
        "key_configured": provider.key_configured,
        "key_hint": provider.key_hint,
        "created_at": provider.created_at.isoformat() if provider.created_at else None,
        "updated_at": provider.updated_at.isoformat() if provider.updated_at else None,
    }


def _model_provider_id(path: str) -> str | None:
    """从服务商详情路径提取一个不含额外层级的稳定标识。

    Args:
        path: 不含查询参数的 HTTP 路径。

    Returns:
        匹配详情路由时返回 ID，否则返回 ``None``。
    """
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[:2] == ["api", "model-providers"] and parts[2]:
        return parts[2]
    return None


def _model_provider_test_id(path: str) -> str | None:
    """从模型探测路径提取服务商标识。

    Args:
        path: 不含查询参数的 HTTP 路径。

    Returns:
        匹配 ``/api/model-providers/{id}/test`` 时返回 ID，否则返回 ``None``。
    """
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[:2] == ["api", "model-providers"]:
        if parts[2] and parts[3] == "test":
            return parts[2]
    return None


def _parse_limit(payload: dict[str, object]) -> int | None:
    """把前端采样模式和自定义数量转换为统一样本上限。

    Returns:
        全量模式或空限制返回 ``None``，快速模式返回 5，自定义模式返回整数。

    Raises:
        ValueError: 自定义限制无法转换为整数。
    """
    # 预设模式优先于自定义字段，确保前端切换单选项时不会残留旧数量。
    sample_mode = str(payload.get("sample_mode", "custom"))
    if sample_mode == "all":
        return None
    if sample_mode == "quick":
        return 5

    # 自定义模式允许空值表达全量执行，非空值交给整数转换提供明确错误。
    raw_limit = payload.get("limit")
    if raw_limit in (None, ""):
        return None
    return int(raw_limit)


def _task_request(
    payload: object,
    provider_repository: ModelProviderRepository | None = None,
) -> TaskRequest:
    """校验任务创建正文并转换为可持久化请求。

    Args:
        payload: 浏览器提交并已完成 JSON 解析的值。
        provider_repository: API 模型请求用于解析脱敏配置的服务商仓储。

    Returns:
        字段完整且样本数量合法的任务请求。

    Raises:
        ValueError: 评测类型、Agent 组合或通用运行字段不合法。
    """
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    evaluation_type = str(payload.get("evaluation_type", "model"))
    if evaluation_type not in {"model", "agent"}:
        raise ValueError("evaluation_type must be one of: model, agent")

    # 套件只适用于模型评测；校验后用首个成员填充旧版必填 dataset 兼容字段。
    suite_id: str | None = None
    raw_suite_id = payload.get("suite_id")
    if evaluation_type == "model" and raw_suite_id not in (None, ""):
        suite_id = str(raw_suite_id).strip()
        try:
            suite = get_suite_spec(suite_id)
        except KeyError as exc:
            raise ValueError(_exception_message(exc)) from exc
        default_dataset = suite.benchmark_ids[0]
    else:
        default_dataset = ""

    # 数据集与模型共同标识一次执行目标，两者都必须在入队前完成校验。
    dataset = str(payload.get("dataset", default_dataset)).strip()
    model = str(payload.get("model", "")).strip()
    if not dataset or not model:
        raise ValueError("dataset and model are required")

    adapter = str(payload.get("adapter", "ollama")).strip()
    if adapter not in {"ollama", "oracle", "openai-compatible"}:
        raise ValueError("adapter must be one of: ollama, oracle, openai-compatible")

    # API 模型只接受服务商引用，地址始终取仓储快照，浏览器无法覆盖为任意目标。
    raw_provider_id = payload.get("provider_id")
    provider_id: str | None = None
    base_url = str(payload.get("base_url", DEFAULT_OLLAMA_BASE_URL))
    if adapter == "openai-compatible":
        if not isinstance(raw_provider_id, str) or not raw_provider_id.strip():
            raise ValueError("provider_id is required for openai-compatible adapter")
        if provider_repository is None:
            raise ValueError("model provider repository is not configured")
        provider_id = raw_provider_id.strip()
        try:
            provider = provider_repository.get(provider_id)
        except ModelProviderNotFoundError as exc:
            raise ValueError(_exception_message(exc)) from exc
        if not provider.key_configured:
            raise ValueError(f"model provider {provider_id} has no API Key")
        base_url = provider.base_url
    elif raw_provider_id not in (None, ""):
        raise ValueError("provider_id is only valid for openai-compatible adapter")
    agent_framework: str | None = None
    agent_difficulty: str | None = None
    if evaluation_type == "agent":
        # Agent 只接受已实现的 Pi、Coding Mini 和固定模型来源，拒绝虚假可用选项。
        agent_framework = str(payload.get("agent_framework", ""))
        if agent_framework != "pi":
            raise ValueError("agent_framework must be pi")
        if dataset != "coding_mini":
            raise ValueError("agent dataset must be coding_mini")
        if adapter not in {"ollama", "openai-compatible"}:
            raise ValueError("agent adapter must be one of: ollama, openai-compatible")
        supported_api_providers = {"deepseek", "siliconflow"}
        if adapter == "openai-compatible" and provider_id not in supported_api_providers:
            raise ValueError("agent API provider must be one of: deepseek, siliconflow")
        if adapter == "openai-compatible" and base_url != BUILTIN_PROVIDERS[str(provider_id)][1]:
            raise ValueError("agent API provider must use its official base URL")
        agent_difficulty = str(payload.get("agent_difficulty", "all"))
        if agent_difficulty not in {"all", "easy", "medium", "hard"}:
            raise ValueError("agent_difficulty must be one of: all, easy, medium, hard")
    else:
        if "agent_difficulty" in payload:
            raise ValueError("agent_difficulty is only valid for agent evaluations")
        try:
            get_benchmark_spec(dataset)
        except KeyError as exc:
            raise ValueError(_exception_message(exc)) from exc
    sample_mode = str(payload.get("sample_mode", "custom"))
    if sample_mode not in {"all", "quick", "custom"}:
        raise ValueError("sample_mode must be one of: all, quick, custom")
    if evaluation_type == "agent" and sample_mode != "all":
        raise ValueError("agent sample_mode must be all")

    # 预设模式由执行器统一解释；只有自定义模式要求并持久化显式正整数。
    limit = None
    if sample_mode == "custom":
        raw_limit = payload.get("limit")
        if isinstance(raw_limit, bool):
            raise ValueError("limit must be a positive integer")
        try:
            limit = int(raw_limit) if raw_limit is not None else None
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be a positive integer") from exc
        if limit is None or limit <= 0:
            raise ValueError("limit must be a positive integer")

    return TaskRequest(
        dataset=dataset,
        adapter=adapter,
        model=model,
        base_url=base_url,
        sample_mode=sample_mode,
        subject=(
            "all" if suite_id is not None else str(payload.get("subject", "all"))
        ),
        limit=limit,
        evaluation_type=evaluation_type,
        agent_framework=agent_framework,
        suite_id=suite_id,
        agent_difficulty=agent_difficulty,
        provider_id=provider_id,
    )


def _task_detail_id(path: str) -> str | None:
    """从任务详情路径中提取单个非空任务标识。

    Args:
        path: 不含查询参数的 HTTP 路径。

    Returns:
        匹配详情路由时返回任务标识，否则返回 ``None``。
    """
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[:2] == ["api", "evaluations"] and parts[2]:
        return parts[2]
    return None


def _cancel_task_id(path: str) -> str | None:
    """从任务取消路径中提取单个非空任务标识。

    Args:
        path: 不含查询参数的 HTTP 路径。

    Returns:
        匹配取消路由时返回任务标识，否则返回 ``None``。
    """
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[:2] == ["api", "evaluations"]:
        if parts[2] and parts[3] == "cancel":
            return parts[2]
    return None


def _node_detail_route(path: str) -> tuple[str, str] | None:
    """匹配任务内节点详情路径。

    Args:
        path: 不含查询参数的 HTTP 路径。

    Returns:
        精确匹配时返回任务与节点标识，否则返回 ``None``。
    """
    # 固定分段数量避免把样本、重试或额外尾路径误识别为节点详情。
    parts = path.strip("/").split("/")
    if len(parts) == 5 and parts[:2] == ["api", "evaluations"] and parts[3] == "nodes":
        if parts[2] and parts[4]:
            return parts[2], parts[4]
    return None


def _node_samples_route(path: str) -> tuple[str, str] | None:
    """匹配任务内节点样本分页路径。

    Args:
        path: 不含查询参数的 HTTP 路径。

    Returns:
        精确匹配时返回任务与节点标识，否则返回 ``None``。
    """
    # 只接受固定 samples 尾段，防止详情路由或未知子资源被错误分派。
    parts = path.strip("/").split("/")
    if (
        len(parts) == 6
        and parts[:2] == ["api", "evaluations"]
        and parts[3] == "nodes"
        and parts[5] == "samples"
        and parts[2]
        and parts[4]
    ):
        return parts[2], parts[4]
    return None


def _node_retry_route(path: str) -> tuple[str, str] | None:
    """匹配任务内节点人工重试路径。

    Args:
        path: 不含查询参数的 HTTP 路径。

    Returns:
        精确匹配时返回任务与节点标识，否则返回 ``None``。
    """
    # 重试是显式写操作，只允许固定 retry 尾段进入状态变更处理。
    parts = path.strip("/").split("/")
    if (
        len(parts) == 6
        and parts[:2] == ["api", "evaluations"]
        and parts[3] == "nodes"
        and parts[5] == "retry"
        and parts[2]
        and parts[4]
    ):
        return parts[2], parts[4]
    return None


def _exception_message(exc: Exception) -> str:
    """提取领域异常正文，避免 KeyError 派生异常自动附加引号。

    Args:
        exc: 需要映射到本地 JSON 边界的领域异常。

    Returns:
        适合直接展示的稳定错误消息。
    """
    if exc.args:
        return str(exc.args[0])
    return str(exc)
