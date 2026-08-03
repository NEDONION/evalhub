"""采集隔离评测进程及可选 NVIDIA 设备的真实资源读数。"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Protocol

import psutil

from evalhub.tasks.models import ResourceUsage

GpuSample = tuple[bool, float | None, int | None]


class ProcessLike(Protocol):
    """描述资源采样器依赖的最小 psutil 进程接口。"""

    pid: int

    def cpu_percent(self) -> float:
        """返回进程在最近采样窗口内的 CPU 百分比。"""

    def memory_info(self) -> MemoryInfoLike:
        """返回至少包含 RSS 字段的进程内存快照。"""

    def children(self, *, recursive: bool) -> list[ProcessLike]:
        """返回需要聚合的直接或递归子进程。"""


class MemoryInfoLike(Protocol):
    """描述 psutil 内存快照中任务采样依赖的 RSS 字段。"""

    rss: int


class NvidiaGpuProbe:
    """通过可选的 ``nvidia-smi`` 查询活动设备利用率和显存。"""

    def __init__(
        self,
        *,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        """注入命令执行边界，便于在无 GPU 的测试环境验证降级语义。"""
        self._command_runner = command_runner

    def sample(self) -> GpuSample:
        """读取利用率最高 NVIDIA 设备的当前负载。

        Returns:
            依次包含支持状态、GPU 百分比和显存字节数；不可用时后两项为空。
        """
        command = [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used",
            "--format=csv,noheader,nounits",
        ]
        try:
            # 查询设置短超时，避免驱动异常拖慢任务状态采样和取消响应。
            completed = self._command_runner(
                command,
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            if completed.returncode != 0:
                return False, None, None
            devices = self._parse_devices(completed.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
            # 缺少命令、驱动无响应或格式异常都表示当前平台没有可靠 GPU 读数。
            return False, None, None
        if not devices:
            return False, None, None

        # 单 Worker 窗口选择利用率最高的活动设备，避免多卡均值掩盖真实峰值。
        percent, memory_mebibytes = max(devices, key=lambda item: item[0])
        return True, percent, memory_mebibytes * 1024 * 1024

    @staticmethod
    def _parse_devices(output: str) -> list[tuple[float, int]]:
        """把 nvidia-smi CSV 正文解析为利用率和显存 MiB 列表。

        Raises:
            ValueError: 任一非空设备行缺列或包含非数值内容。
        """
        devices: list[tuple[float, int]] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            # 查询格式固定为两列；严格解析可以防止驱动提示文本被误当资源数字。
            percent_text, memory_text = (part.strip() for part in line.split(",", maxsplit=1))
            devices.append((float(percent_text), int(memory_text)))
        return devices


class ProcessResourceSampler:
    """聚合一个评测根进程与其递归子进程的 CPU 和 RSS。"""

    def __init__(
        self,
        *,
        process_factory: Callable[[int], ProcessLike] = psutil.Process,
        gpu_probe: Callable[[], GpuSample] | None = None,
    ) -> None:
        """注入进程和 GPU 探测边界，生产环境默认使用 psutil 与 NVIDIA CLI。"""
        self._process_factory = process_factory
        self._gpu_probe = gpu_probe or NvidiaGpuProbe().sample
        self._process_cache: dict[int, ProcessLike] = {}
        self._root_process_id: int | None = None

    def sample(self, process_id: int) -> ResourceUsage:
        """采集指定评测进程树和当前活动 GPU 的资源占用。

        Args:
            process_id: 隔离评测子进程的操作系统 PID。

        Returns:
            进程树聚合 CPU、RSS 与可选 GPU 设备读数。
        """
        # psutil 的非阻塞 CPU 读数依赖同一对象的前后采样，任务切换时才清空缓存。
        if self._root_process_id != process_id:
            self._process_cache.clear()
            self._root_process_id = process_id
        try:
            root = self._process_cache.get(process_id) or self._process_factory(process_id)
            discovered = [root, *root.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
            # 进程恰好退出时返回零 CPU/RSS，终态仍由执行器消息决定。
            self._process_cache.clear()
            discovered = []

        # 子进程列表可能随模型运行变化；按 PID 复用存活对象并淘汰已经消失的旧对象。
        active_processes = {
            process.pid: self._process_cache.get(process.pid, process)
            for process in discovered
        }
        self._process_cache = active_processes

        cpu_percent = 0.0
        memory_bytes = 0
        unavailable_processes: list[int] = []
        for process in active_processes.values():
            try:
                # 单任务资源等于根进程与子进程之和，多核 CPU 合计允许超过 100%。
                cpu_percent += float(process.cpu_percent())
                memory_bytes += int(process.memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
                unavailable_processes.append(process.pid)

        # 采样期间退出的进程立即移除，若相同 PID 被新子进程复用，下次可重新建立基线。
        for unavailable_process_id in unavailable_processes:
            self._process_cache.pop(unavailable_process_id, None)

        gpu_supported, gpu_percent, gpu_memory_bytes = self._gpu_probe()
        return ResourceUsage(
            cpu_percent=round(cpu_percent, 2),
            memory_bytes=memory_bytes,
            gpu_supported=gpu_supported,
            gpu_percent=gpu_percent,
            gpu_memory_bytes=gpu_memory_bytes,
        )
