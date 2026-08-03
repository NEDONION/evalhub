import unittest

from evalhub.cli import build_parser


class CliParserTest(unittest.TestCase):
    def test_run_benchmark_limit_defaults_to_none_for_full_dataset(self) -> None:
        args = build_parser().parse_args(["run-benchmark", "--dataset", "gsm8k"])

        self.assertIsNone(args.limit)

    def test_run_benchmark_accepts_explicit_limit(self) -> None:
        args = build_parser().parse_args(["run-benchmark", "--dataset", "gsm8k", "--limit", "5"])

        self.assertEqual(args.limit, 5)


if __name__ == "__main__":
    unittest.main()
