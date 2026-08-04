"""通过显式启用的真实 Docker 镜像验证 HumanEval 正确与错误候选。"""

import os

import pytest

from evalhub.benchmarks.humaneval import DockerHumanEvalSandbox, HumanEvalProblem

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("EVALHUB_RUN_DOCKER_TESTS") != "1",
        reason="set EVALHUB_RUN_DOCKER_TESTS=1 to run Docker integration",
    ),
]


def test_fixed_docker_image_accepts_canonical_and_rejects_incorrect_completion() -> None:
    """固定镜像应让标准实现通过、错误实现失败且只返回安全短原因。"""
    problem = HumanEvalProblem(
        sample_id="integration_humaneval",
        source_key="HumanEval/integration",
        prompt="def add(a, b):\n",
        canonical_solution="    return a + b\n",
        test="def check(candidate):\n    assert candidate(2, 3) == 5\n",
        entry_point="add",
        input_zh="实现两个数字相加。",
    )
    sandbox = DockerHumanEvalSandbox()

    # 两次调用都经过真实 Docker 边界，测试进程从不导入或执行候选源码。
    passed = sandbox.run(problem, problem.canonical_solution)
    failed = sandbox.run(problem, "    return a - b\n")

    assert passed.passed is True
    assert passed.reason is None
    assert failed.passed is False
    assert failed.reason == "verification_failed"
