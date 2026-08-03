"""提供本地 React 静态资源与 EvalHub JSON API 的轻量多线程 HTTP 服务。"""

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from evalhub.cli import run_real_benchmark
from evalhub.datasets import dataset_catalog, load_samples, prepare_dataset
from evalhub.ollama import DEFAULT_OLLAMA_BASE_URL, DEFAULT_OLLAMA_MODEL, get_ollama_status


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

    def __init__(
        self, *args: object, directory: str | None = None, **kwargs: object
    ) -> None:
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
        """路由健康、数据集、Ollama 状态和 React 静态资源请求。"""
        # 先解析路径与查询参数，API 路由不受静态文件路径重写影响。
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"status": "ok", "service": "evalhub"})
            return
        if parsed.path == "/api/datasets":
            self._json(self._dataset_status())
            return
        if parsed.path == "/api/ollama/status":
            # 查询参数允许控制台切换模型与服务地址，同时保留安全默认值。
            query = parse_qs(parsed.query)
            model = _first(query, "model", DEFAULT_OLLAMA_MODEL)
            base_url = _first(query, "base_url", DEFAULT_OLLAMA_BASE_URL)
            self._json(get_ollama_status(model=model, base_url=base_url))
            return
        # 根路径映射到 Vite 构建入口，其他路径继续使用标准静态文件处理逻辑。
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        """处理数据集准备和同步评测两个本地写操作 API。"""
        # 路径先于正文解析，未知端点无需读取可能很大的请求负载。
        parsed = urlparse(self.path)
        if parsed.path == "/api/datasets/prepare":
            payload = self._read_json()
            dataset = str(payload.get("dataset", "gsm8k"))
            try:
                # 下载和缓存由数据集层执行，响应只暴露状态、名称和最终本地路径。
                path = prepare_dataset(dataset)
                self._json({"ok": True, "dataset": dataset, "path": str(path)})
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
                    model=str(payload.get("model", "qwen2.5:0.5b")),
                    base_url=str(payload.get("base_url", "http://127.0.0.1:11434")),
                    limit=limit,
                    subject=str(payload.get("subject", "abstract_algebra")),
                )
                # 成功响应把报告放在 ``result`` 下，为前端保留统一的 ``ok`` 判定字段。
                self._json({"ok": True, "result": result})
            except Exception as exc:
                # 模型、数据或评测异常在本地 API 边界统一转换，正文保留原始诊断消息。
                self._json({"ok": False, "error": str(exc)}, status=500)
            return

        # 所有未注册 POST 路径返回结构化 404，避免落入静态文件处理器。
        self._json({"ok": False, "error": "not found"}, status=404)

    def _dataset_status(self) -> dict[str, object]:
        """汇总数据集元数据、本地准备状态和可读取样本数。"""
        # 按目录稳定顺序构建新列表，避免修改不可变的数据集规格对象。
        datasets = []
        for spec in dataset_catalog().values():
            path = Path(spec.local_path)
            prepared = path.exists() and (path.is_file() or any(path.glob("*")))
            # 未准备的数据集不尝试加载；准备完成后尽可能计算控制台展示用样本数。
            sample_count = None
            if prepared:
                try:
                    # 当前公开测试集规模可控，上限用于防止异常缓存导致无界读取。
                    sample_count = len(
                        load_samples(
                            spec.name,
                            limit=100000,
                            subject="abstract_algebra" if spec.name == "mmlu" else None,
                        )
                    )
                except Exception:
                    # 状态接口应继续展示损坏或不完整的数据集，由空样本数提示需要重新准备。
                    sample_count = None
            # 响应同时包含来源与本地信息，前端无需再拼接目录规格。
            datasets.append(
                {
                    "name": spec.name,
                    "display_name": spec.display_name,
                    "task_type": spec.task_type,
                    "evaluator_type": spec.evaluator_type,
                    "homepage": spec.homepage,
                    "source_url": spec.source_url,
                    "local_path": spec.local_path,
                    "description": spec.description,
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
        return json.loads(self.rfile.read(length).decode("utf-8"))

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
    # 多线程服务器避免长时间评测请求阻塞健康检查和静态资源访问。
    server = ThreadingHTTPServer((host, port), EvalHubRequestHandler)
    print(f"EvalHub local console: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # 用户主动中断属于正常本地退出流程，只输出提示而不传播堆栈。
        print("\nEvalHub local console stopped.")
    finally:
        # 无论正常中断还是服务异常都关闭监听套接字，避免端口残留占用。
        server.server_close()


def _first(query: dict[str, list[str]], key: str, default: str) -> str:
    """读取查询参数的第一个非空值，缺失时返回默认值。"""
    # ``parse_qs`` 为每个键返回列表，本地 API 只接受其中第一个值。
    values = query.get(key)
    if not values:
        return default
    return values[0] or default


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
