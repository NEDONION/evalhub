"""验证评测 CLI 的样本数量参数默认值和显式解析行为。"""

import unittest

from evalhub.cli import build_parser


class CliParserTest(unittest.TestCase):
    """保护 ``run-benchmark`` 参数解析与全量评测默认语义。"""

    def test_run_benchmark_limit_defaults_to_none_for_full_dataset(self) -> None:
        """未提供 ``--limit`` 时应使用 ``None`` 表示完整数据集。"""
        # 只提供必需子命令与数据集，确保断言覆盖解析器自身的默认配置。
        args = build_parser().parse_args(["run-benchmark", "--dataset", "gsm8k"])

        # ``None`` 会一直传到加载器，不能被静默替换为小规模试跑数量。
        self.assertIsNone(args.limit)

    def test_run_benchmark_accepts_explicit_limit(self) -> None:
        """显式 ``--limit`` 应转换为整数并覆盖全量默认值。"""
        # 使用字符串形式模拟真实命令行输入，验证 argparse 类型转换配置。
        args = build_parser().parse_args(["run-benchmark", "--dataset", "gsm8k", "--limit", "5"])

        # 下游加载器期望整数上限，因此断言同时保护值和隐式类型转换结果。
        self.assertEqual(args.limit, 5)


if __name__ == "__main__":
    # 保留 unittest 直接执行入口，方便在不经过 pytest 时单独排查解析器测试。
    unittest.main()
