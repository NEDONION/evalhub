from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from evalhub.cli import run_real_benchmark
from evalhub.datasets import dataset_catalog, load_samples, prepare_dataset
from evalhub.ollama import DEFAULT_OLLAMA_BASE_URL, DEFAULT_OLLAMA_MODEL, get_ollama_status


def frontend_directory(project_root: Path) -> Path:
    directory = project_root / "frontend" / "dist"
    if not (directory / "index.html").is_file():
        raise FileNotFoundError(
            "React frontend build not found. Run: npm --prefix frontend run build"
        )
    return directory


class EvalHubRequestHandler(SimpleHTTPRequestHandler):
    server_version = "EvalHubLocal/0.1"

    def __init__(self, *args, directory: str | None = None, **kwargs) -> None:
        root = Path(__file__).resolve().parents[2]
        static_directory = Path(directory) if directory else frontend_directory(root)
        super().__init__(*args, directory=str(static_directory), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"status": "ok", "service": "evalhub"})
            return
        if parsed.path == "/api/datasets":
            self._json(self._dataset_status())
            return
        if parsed.path == "/api/ollama/status":
            query = parse_qs(parsed.query)
            model = _first(query, "model", DEFAULT_OLLAMA_MODEL)
            base_url = _first(query, "base_url", DEFAULT_OLLAMA_BASE_URL)
            self._json(get_ollama_status(model=model, base_url=base_url))
            return
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/datasets/prepare":
            payload = self._read_json()
            dataset = str(payload.get("dataset", "gsm8k"))
            try:
                path = prepare_dataset(dataset)
                self._json({"ok": True, "dataset": dataset, "path": str(path)})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/evaluations/run":
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
                self._json({"ok": True, "result": result})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, status=500)
            return

        self._json({"ok": False, "error": "not found"}, status=404)

    def _dataset_status(self) -> dict[str, object]:
        datasets = []
        for spec in dataset_catalog().values():
            path = Path(spec.local_path)
            prepared = path.exists() and (path.is_file() or any(path.glob("*")))
            sample_count = None
            if prepared:
                try:
                    sample_count = len(
                        load_samples(
                            spec.name,
                            limit=100000,
                            subject="abstract_algebra" if spec.name == "mmlu" else None,
                        )
                    )
                except Exception:
                    sample_count = None
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
        return {"datasets": datasets}

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[evalhub] {self.address_string()} - {format % args}")


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), EvalHubRequestHandler)
    print(f"EvalHub local console: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEvalHub local console stopped.")
    finally:
        server.server_close()


def _first(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    if not values:
        return default
    return values[0] or default


def _parse_limit(payload: dict[str, object]) -> int | None:
    sample_mode = str(payload.get("sample_mode", "custom"))
    if sample_mode == "all":
        return None
    if sample_mode == "quick":
        return 5

    raw_limit = payload.get("limit")
    if raw_limit in (None, ""):
        return None
    return int(raw_limit)
