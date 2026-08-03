"""验证评测进程 CPU、内存与可选 NVIDIA GPU 资源采集。"""

from subprocess import CompletedProcess
from types import SimpleNamespace

from evalhub.tasks.resources import AppleGpuProbe, NvidiaGpuProbe, ProcessResourceSampler


class FakeProcess:
    """提供 psutil 采样器所需最小进程行为的确定性替身。"""

    def __init__(
        self,
        process_id: int,
        cpu_percent: float,
        memory_bytes: int,
        *,
        children: list["FakeProcess"] | None = None,
    ) -> None:
        """保存固定 CPU、RSS 与递归子进程列表。"""
        self.pid = process_id
        self._cpu_percent = cpu_percent
        self._memory_bytes = memory_bytes
        self._children = children or []

    def cpu_percent(self) -> float:
        """返回本进程在测试采样窗口中的固定 CPU 百分比。"""
        return self._cpu_percent

    def memory_info(self) -> SimpleNamespace:
        """返回包含固定 RSS 字节数的轻量内存信息对象。"""
        return SimpleNamespace(rss=self._memory_bytes)

    def children(self, *, recursive: bool) -> list["FakeProcess"]:
        """返回配置的子进程并验证采样器要求递归聚合。"""
        assert recursive is True
        return self._children


def test_resource_sampler_aggregates_process_and_children() -> None:
    """CPU 与 RSS 应覆盖评测根进程及其全部直接或间接子进程。"""
    child = FakeProcess(101, 25.0, 2048)
    root = FakeProcess(100, 50.0, 1024, children=[child])
    sampler = ProcessResourceSampler(
        process_factory=lambda process_id: root,
        gpu_probe=lambda: (False, None, None),
    )

    usage = sampler.sample(100)

    assert usage.cpu_percent == 75.0
    assert usage.memory_bytes == 3072
    assert usage.gpu_supported is False
    assert usage.gpu_percent is None


class SequencedFakeProcess(FakeProcess):
    """模拟 psutil 首次 CPU 调用为零、后续调用才产生有效读数。"""

    def __init__(self, process_id: int, cpu_values: list[float]) -> None:
        """保存按采样次数依次返回的 CPU 序列。"""
        super().__init__(process_id, 0.0, 1024)
        self._cpu_values = iter(cpu_values)

    def cpu_percent(self) -> float:
        """返回当前进程对象下一次非阻塞 CPU 读数。"""
        return next(self._cpu_values)


def test_resource_sampler_reuses_process_objects_between_cpu_samples() -> None:
    """连续采样应复用同一 psutil 对象，使第二次 CPU 百分比不再是初始化零值。"""
    created_processes: list[SequencedFakeProcess] = []

    def process_factory(process_id: int) -> SequencedFakeProcess:
        """每次构造都会返回需要先预热的对象，用于识别错误的重复构造。"""
        process = SequencedFakeProcess(process_id, [0.0, 37.5])
        created_processes.append(process)
        return process

    sampler = ProcessResourceSampler(
        process_factory=process_factory,
        gpu_probe=lambda: (False, None, None),
    )

    first_usage = sampler.sample(200)
    second_usage = sampler.sample(200)

    assert first_usage.cpu_percent == 0.0
    assert second_usage.cpu_percent == 37.5
    assert len(created_processes) == 1


def test_resource_sampler_uses_system_cpu_for_external_ollama_workload() -> None:
    """Ollama 模式应显示包含独立模型服务的本机 CPU，而不是只看轻量 Python 客户端。"""
    root = FakeProcess(300, 0.3, 1024)
    sampler = ProcessResourceSampler(
        process_factory=lambda process_id: root,
        gpu_probe=lambda: (False, None, None),
        include_system_cpu=True,
        system_cpu_probe=lambda: 87.5,
    )

    usage = sampler.sample(300)

    assert usage.cpu_percent == 87.5


def test_apple_gpu_probe_reads_agx_utilization_and_unified_memory() -> None:
    """Apple Silicon 应从 AGX PerformanceStatistics 读取系统 GPU 与统一内存。"""
    output = (
        '+-o AGXAcceleratorG14X <class AGXAcceleratorG14X>\n'
        '  "PerformanceStatistics" = {'
        '"Renderer Utilization %"=58,"Device Utilization %"=84,'
        '"In use system memory"=3379724288}\n'
    )

    def command_runner(*args: object, **kwargs: object) -> CompletedProcess[str]:
        """返回包含 M 系列 AGX 设备统计的稳定 ioreg 文本。"""
        return CompletedProcess(args=[], returncode=0, stdout=output, stderr="")

    supported, percent, memory_bytes = AppleGpuProbe(
        command_runner=command_runner
    ).sample()

    assert supported is True
    assert percent == 84.0
    assert memory_bytes == 3379724288


def test_nvidia_probe_uses_busiest_device_and_converts_megabytes() -> None:
    """多卡输出应选择利用率最高设备并把显存 MiB 转成字节。"""
    def command_runner(*args: object, **kwargs: object) -> CompletedProcess[str]:
        """返回两张虚拟 NVIDIA 卡的稳定查询结果。"""
        return CompletedProcess(args=[], returncode=0, stdout="20, 512\n75, 1024\n", stderr="")

    supported, percent, memory_bytes = NvidiaGpuProbe(command_runner=command_runner).sample()

    assert supported is True
    assert percent == 75.0
    assert memory_bytes == 1024 * 1024 * 1024


def test_nvidia_probe_reports_unsupported_when_command_is_missing() -> None:
    """系统没有 nvidia-smi 时必须显式降级而不是伪造零占用。"""
    def missing_command(*args: object, **kwargs: object) -> CompletedProcess[str]:
        """模拟操作系统无法找到 NVIDIA 查询命令。"""
        raise FileNotFoundError("nvidia-smi")

    supported, percent, memory_bytes = NvidiaGpuProbe(command_runner=missing_command).sample()

    assert supported is False
    assert percent is None
    assert memory_bytes is None
