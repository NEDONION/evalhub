"""验证精确匹配评测器的文本归一化和不匹配结果语义。"""

from evalhub.evaluators import ExactMatchEvaluator


def test_exact_match_normalizes_case_and_whitespace() -> None:
    """默认配置应忽略首尾空白与 Unicode 大小写差异。"""
    # 使用默认评测器配置，确保测试直接保护面向调用方的开箱行为。
    evaluator = ExactMatchEvaluator()

    # 预测同时包含大小写与边界空白差异，参考答案保持规范形式。
    result = evaluator.evaluate("  Answer  ", "answer")

    # 成功结果必须保留稳定指标名、满分值，并且不产生误导性失败原因。
    assert result.metric == "exact_match"
    assert result.score == 1.0
    assert result.reason is None


def test_exact_match_returns_zero_for_mismatch() -> None:
    """归一化后仍不同的文本应返回零分和可诊断原因。"""
    # 数值字符串不同且不存在可被默认规则消除的格式差异。
    evaluator = ExactMatchEvaluator()

    # 通过公开评分接口生成领域指标结果，不依赖内部归一化实现细节。
    result = evaluator.evaluate("4", "5")

    # 失败既要反映为零分，也必须提供原因供样本报告解释。
    assert result.score == 0.0
    assert result.reason is not None
