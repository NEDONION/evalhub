"""验证 Ollama 安装、运行、模型存在性与推荐选项的状态归一化。"""

import unittest
from unittest.mock import patch
from urllib.error import URLError


class OllamaStatusTest(unittest.TestCase):
    """通过替换命令探测和 HTTP 边界测试所有本地状态分支。"""

    def test_recommended_catalog_separates_model_and_agent_candidates(self) -> None:
        """推荐目录应覆盖答题与 Agent 候选，并隔离不兼容工具模型。"""
        from evalhub.ollama import DEFAULT_OLLAMA_MODEL, RECOMMENDED_OLLAMA_MODELS

        # 目录顺序决定下拉框推荐顺序，用手写期望约束两种评测的真实候选边界。
        catalog = {str(item["name"]): item for item in RECOMMENDED_OLLAMA_MODELS}
        self.assertEqual(DEFAULT_OLLAMA_MODEL, "granite4.1:3b")
        self.assertEqual(
            list(catalog),
            [
                "granite4.1:3b",
                "qwen3:4b",
                "qwen3:8b",
                "qwen3:14b",
                "ministral-3:8b",
                "gemma4:12b",
                "lfm2.5:8b",
                "north-mini-code-1.0:q4_K_M",
                "qwen2.5:0.5b",
                "qwen2.5:1.5b",
                "deepseek-r1:1.5b",
                "qwen2.5-coder:7b",
            ],
        )
        self.assertEqual(catalog["qwen2.5-coder:7b"]["evaluation_types"], ["model"])
        self.assertEqual(
            catalog["ministral-3:8b"]["evaluation_types"], ["model", "agent"]
        )
        self.assertEqual(catalog["north-mini-code-1.0:q4_K_M"]["capability_label"], "Agent 编码")

    def test_status_reports_not_installed_when_command_missing(self) -> None:
        """找不到可执行命令时应报告未安装且不得尝试服务请求。"""
        from evalhub.ollama import get_ollama_status

        # 只替换命令查找即可触发最早返回分支，避免产生任何真实网络访问。
        with patch("evalhub.ollama.find_ollama_command", return_value=None):
            status = get_ollama_status(model="qwen2.5:0.5b")

        # 三个布尔字段共同区分未安装与已安装但服务停止的状态。
        self.assertEqual(status["installed"], False)
        self.assertEqual(status["running"], False)
        self.assertEqual(status["model_present"], False)

    def test_status_reports_running_and_model_present(self) -> None:
        """标签接口包含目标模型时应报告服务运行且模型可用。"""
        from evalhub.ollama import get_ollama_status

        # 响应同时包含目标与其他模型，验证完整模型列表顺序也被保留。
        response = _Response(
            b'{"models":[{"name":"qwen2.5:0.5b"},{"name":"llama3.2:1b"}]}'
        )
        # 同时替换命令和 HTTP 响应，使断言只覆盖状态解析而不依赖本机环境。
        with (
            patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
            patch("evalhub.ollama.urlopen", return_value=response),
        ):
            status = get_ollama_status(model="qwen2.5:0.5b")

        # 成功状态必须同时反映安装、运行、模型命中和服务返回的模型标签。
        self.assertEqual(status["installed"], True)
        self.assertEqual(status["running"], True)
        self.assertEqual(status["model_present"], True)
        self.assertEqual(status["models"], ["qwen2.5:0.5b", "llama3.2:1b"])

    def test_status_includes_installed_and_recommended_model_options(self) -> None:
        """模型选项应优先已安装项并补充尚未安装的推荐模型。"""
        from evalhub.ollama import get_ollama_status

        # 仅返回一个本机模型，以便同时断言已安装和未安装推荐项的状态。
        response = _Response(b'{"models":[{"name":"custom-local:latest"}]}')
        with (
            patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
            patch("evalhub.ollama.urlopen", return_value=response),
        ):
            status = get_ollama_status(model="ministral-3:8b")

        # 按名称建立临时索引，让断言不依赖推荐列表中其他模型的展示顺序。
        options = status["model_options"]
        option_by_name = {option["name"]: option for option in options}
        self.assertEqual(option_by_name["custom-local:latest"]["installed"], True)
        self.assertEqual(
            option_by_name["custom-local:latest"]["evaluation_types"], ["model", "agent"]
        )
        self.assertEqual(option_by_name["custom-local:latest"]["capability_label"], "本机模型")
        self.assertEqual(
            option_by_name["custom-local:latest"]["benchmark_protocol"], "unsupported"
        )
        self.assertIn(
            "未注册", option_by_name["custom-local:latest"]["benchmark_protocol_reason"]
        )
        self.assertEqual(option_by_name["ministral-3:8b"]["installed"], False)
        self.assertEqual(
            option_by_name["ministral-3:8b"]["benchmark_protocol"], "static_only"
        )
        self.assertIn("函数调用", option_by_name["ministral-3:8b"]["description"])

    def test_installed_registered_model_reports_verified_protocol(self) -> None:
        """已安装且已登记模型应向控制台报告可执行的 Benchmark 协议。"""
        from evalhub.ollama import get_ollama_status

        response = _Response(b'{"models":[{"name":"granite3.3:8b"}]}')
        with (
            patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
            patch("evalhub.ollama.urlopen", return_value=response),
        ):
            status = get_ollama_status(model="granite3.3:8b")

        option = next(
            item for item in status["model_options"] if item["name"] == "granite3.3:8b"
        )
        self.assertEqual(option["benchmark_protocol"], "verified")
        self.assertEqual(option["benchmark_protocol_version"], "ollama-generate-v1")

    def test_installed_model_size_overrides_catalog_estimate(self) -> None:
        """已安装模型必须展示 Ollama 返回的真实磁盘大小。"""
        from evalhub.ollama import get_ollama_status

        response = _Response(
            b'{"models":[{"name":"qwen2.5:1.5b","size":987654321}]}'
        )
        with (
            patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
            patch("evalhub.ollama.urlopen", return_value=response),
        ):
            status = get_ollama_status(model="qwen2.5:1.5b")

        option = next(
            item for item in status["model_options"] if item["name"] == "qwen2.5:1.5b"
        )
        self.assertEqual(option["size_bytes"], 987654321)
        self.assertEqual(option["size_kind"], "actual")

    def test_uninstalled_recommended_model_exposes_estimated_size(self) -> None:
        """未下载的推荐模型必须提供明确标注的预估大小。"""
        from evalhub.ollama import get_ollama_status

        response = _Response(b'{"models":[]}')
        with (
            patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
            patch("evalhub.ollama.urlopen", return_value=response),
        ):
            status = get_ollama_status(model="granite4.1:3b")

        option = next(
            item for item in status["model_options"] if item["name"] == "granite4.1:3b"
        )
        self.assertEqual(option["size_bytes"], 2_100_000_000)
        self.assertEqual(option["size_kind"], "estimated")

    def test_status_reports_not_running_when_api_unreachable(self) -> None:
        """命令存在但标签接口不可达时应报告已安装且服务未运行。"""
        from evalhub.ollama import get_ollama_status

        # 保留有效命令路径并让 HTTP 边界失败，精确模拟服务尚未启动场景。
        with (
            patch("evalhub.ollama.find_ollama_command", return_value="/usr/local/bin/ollama"),
            patch("evalhub.ollama.urlopen", side_effect=URLError("connection refused")),
        ):
            status = get_ollama_status(model="qwen2.5:0.5b")

        # 该分支与未安装状态的关键差异是 ``installed`` 仍为真。
        self.assertEqual(status["installed"], True)
        self.assertEqual(status["running"], False)
        self.assertEqual(status["model_present"], False)


class _Response:
    """模拟 ``urlopen`` 返回的可进入上下文并可读取字节响应。"""

    def __init__(self, body: bytes) -> None:
        """保存后续 ``read`` 调用需要返回的固定响应正文。"""
        # 字节正文与真实 HTTP 响应接口一致，避免测试绕过 UTF-8 解码路径。
        self.body = body

    def __enter__(self) -> "_Response":
        """进入上下文时返回当前响应对象供状态函数读取。"""
        # 真实 ``urlopen`` 响应使用同样的上下文管理协议。
        return self

    def __exit__(self, *args: object) -> None:
        """退出上下文时不抑制异常并保持无资源副作用。"""
        # 测试替身没有真实套接字需要关闭，返回 ``None`` 表示异常继续传播。
        return None

    def read(self) -> bytes:
        """返回构造时提供的固定 UTF-8 JSON 字节正文。"""
        # 每次读取返回相同数据，使状态解析测试完全确定且可重复执行。
        return self.body


if __name__ == "__main__":
    # 支持直接运行状态测试文件，便于本地排查不同 Ollama 分支。
    unittest.main()
