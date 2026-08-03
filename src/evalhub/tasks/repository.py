"""使用 SQLite 持久化本地评测任务及其运行状态。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from evalhub.domain.entities import new_id, utc_now
from evalhub.tasks.models import (
    EvaluationNode,
    EvaluationNodeEvent,
    EvaluationSampleCheckpoint,
    EvaluationSamplePage,
    EvaluationTask,
    NodeStatus,
    ResourceUsage,
    TaskRequest,
    TaskStatus,
    WorkflowNodeSpec,
)


class TaskNotFoundError(KeyError):
    """表示调用方查询了不存在的评测任务。"""


class TaskStateError(RuntimeError):
    """表示任务当前状态不允许请求的更新或终态转换。"""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluation_tasks (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'success', 'failed', 'canceled')),
    completed_samples INTEGER NOT NULL DEFAULT 0,
    total_samples INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_message TEXT,
    cpu_percent REAL NOT NULL DEFAULT 0,
    peak_cpu_percent REAL NOT NULL DEFAULT 0,
    memory_bytes INTEGER NOT NULL DEFAULT 0,
    peak_memory_bytes INTEGER NOT NULL DEFAULT 0,
    gpu_supported INTEGER NOT NULL DEFAULT 0,
    gpu_percent REAL,
    peak_gpu_percent REAL,
    gpu_memory_bytes INTEGER,
    peak_gpu_memory_bytes INTEGER,
    benchmark TEXT,
    passed_samples INTEGER,
    average_score REAL,
    result_json TEXT
)
"""

_WORKFLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluation_nodes (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES evaluation_tasks(id) ON DELETE CASCADE,
    node_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'success', 'failed', 'blocked', 'canceled')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    input_json TEXT NOT NULL DEFAULT '{}',
    checkpoint_json TEXT,
    output_json TEXT,
    error_type TEXT,
    error_message TEXT,
    completed_samples INTEGER NOT NULL DEFAULT 0,
    total_samples INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    attempt_started_at TEXT,
    finished_at TEXT,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    UNIQUE (task_id, node_key)
);

CREATE TABLE IF NOT EXISTS evaluation_node_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES evaluation_tasks(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL REFERENCES evaluation_nodes(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    attempt INTEGER NOT NULL,
    actor TEXT NOT NULL,
    message TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_sample_results (
    task_id TEXT NOT NULL REFERENCES evaluation_tasks(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL REFERENCES evaluation_nodes(id) ON DELETE CASCADE,
    sample_key TEXT NOT NULL,
    sample_index INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    attempt_count INTEGER NOT NULL,
    input_json TEXT NOT NULL,
    result_json TEXT,
    last_error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    PRIMARY KEY (node_id, sample_key)
);

CREATE INDEX IF NOT EXISTS idx_evaluation_nodes_task_status
ON evaluation_nodes(task_id, status);
CREATE INDEX IF NOT EXISTS idx_evaluation_node_events_node_created
ON evaluation_node_events(node_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_evaluation_samples_node_order
ON evaluation_sample_results(node_id, sample_index, sample_key);
"""

_NODE_COLUMNS = """
id, task_id, node_key, kind, depends_on_json, status, attempt_count, max_attempts,
input_json, checkpoint_json, output_json, error_type, error_message, completed_samples,
total_samples, created_at, updated_at, started_at, attempt_started_at, finished_at, elapsed_ms
"""

_TASK_COLUMNS = """
id, request_json, status, completed_samples, total_samples, created_at, updated_at,
started_at, finished_at, error_message, cpu_percent, peak_cpu_percent, memory_bytes,
peak_memory_bytes, gpu_supported, gpu_percent, peak_gpu_percent, gpu_memory_bytes,
peak_gpu_memory_bytes, benchmark, passed_samples, average_score, result_json
"""

_SUMMARY_COLUMNS = _TASK_COLUMNS.replace("result_json", "NULL AS result_json")


class SQLiteTaskRepository:
    """提供并发安全的 SQLite 评测任务读写与状态转换。"""

    def __init__(self, database_path: Path) -> None:
        """初始化数据库路径并创建任务表。

        Args:
            database_path: SQLite 文件位置；父目录不存在时会自动创建。
        """
        # 数据库目录由仓储拥有，统一创建可确保首次本地启动无需手工准备。
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(_SCHEMA)
            connection.executescript(_WORKFLOW_SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """创建启用 WAL 和超时控制的短生命周期数据库连接。

        Yields:
            已配置行对象工厂、WAL 和繁忙等待的 SQLite 连接。
        """
        # 每次操作独享连接，避免 HTTP 查询线程与后台 Worker 共享连接对象。
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            # 上下文内部发生异常时 sqlite3 会回滚，正常返回则提交完整状态更新。
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, request: TaskRequest, *, task_id: str | None = None) -> EvaluationTask:
        """创建待执行任务并返回刚持久化的完整记录。

        Args:
            request: 可序列化且可在服务重启后恢复的评测请求。
            task_id: 测试或调度层提供的稳定标识；为空时自动生成。

        Returns:
            状态为 ``pending`` 的新任务。
        """
        identifier = task_id or new_id("job")
        now = utc_now().isoformat()
        # 请求以 JSON 保存，字段新增时无需立即迁移多个关系列。
        payload = json.dumps(asdict(request), ensure_ascii=False, separators=(",", ":"))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_tasks (id, request_json, status, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                (identifier, payload, now, now),
            )
        return self.get(identifier)

    def create_with_nodes(
        self,
        request: TaskRequest,
        nodes: tuple[WorkflowNodeSpec, ...],
        *,
        task_id: str | None = None,
    ) -> EvaluationTask:
        """在一个事务中创建顶层任务、全部节点和节点创建事件。"""
        if not nodes:
            raise ValueError("workflow must contain at least one node")
        node_keys = [node.node_key for node in nodes]
        if len(node_keys) != len(set(node_keys)):
            raise ValueError("workflow node keys must be unique")

        identifier = task_id or new_id("job")
        now = utc_now().isoformat()
        request_json = self._json(asdict(request))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_tasks (id, request_json, status, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                (identifier, request_json, now, now),
            )
            for spec in nodes:
                if spec.max_attempts <= 0:
                    raise ValueError("node max_attempts must be positive")
                unknown = sorted(item for item in spec.depends_on if item not in node_keys)
                if unknown:
                    raise ValueError(
                        f"node {spec.node_key} has unknown dependencies: {', '.join(unknown)}"
                    )
                node_id = new_id("node")
                connection.execute(
                    """
                    INSERT INTO evaluation_nodes (
                        id, task_id, node_key, kind, depends_on_json, status,
                        max_attempts, input_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        node_id,
                        identifier,
                        spec.node_key,
                        spec.kind,
                        self._json(spec.depends_on),
                        spec.max_attempts,
                        self._json(dict(spec.input)),
                        now,
                        now,
                    ),
                )
                self._insert_node_event(
                    connection,
                    task_id=identifier,
                    node_id=node_id,
                    event_type="node_created",
                    from_status=None,
                    to_status="pending",
                    attempt=0,
                    actor="system",
                    message=None,
                    payload=None,
                    created_at=now,
                )
        return self.get(identifier)

    def list_nodes(self, task_id: str) -> list[EvaluationNode]:
        """按工作流创建顺序返回指定任务的全部节点快照。"""
        with self._connection() as connection:
            self._require_task(connection, task_id)
            rows = connection.execute(
                f"SELECT {_NODE_COLUMNS} FROM evaluation_nodes "  # noqa: S608
                "WHERE task_id = ? ORDER BY rowid ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_node(row) for row in rows]

    def get_node(self, node_id: str) -> EvaluationNode:
        """按稳定节点标识读取一个节点快照。"""
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {_NODE_COLUMNS} FROM evaluation_nodes WHERE id = ?",  # noqa: S608
                (node_id,),
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"node not found: {node_id}")
        return self._row_to_node(row)

    def start_node(self, node_id: str, *, actor: str = "worker") -> EvaluationNode:
        """把待执行节点原子切换为运行态并写入开始事件。"""
        now = utc_now().isoformat()
        with self._connection() as connection:
            row = self._require_node_status(connection, node_id, {"pending"}, "start")
            attempt = int(row["attempt_count"]) + 1
            connection.execute(
                """
                UPDATE evaluation_nodes
                SET status = 'running', attempt_count = ?,
                    started_at = COALESCE(started_at, ?), attempt_started_at = ?,
                    finished_at = NULL, error_type = NULL, error_message = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (attempt, now, now, now, node_id),
            )
            self._insert_node_event(
                connection,
                task_id=str(row["task_id"]),
                node_id=node_id,
                event_type="node_started",
                from_status="pending",
                to_status="running",
                attempt=attempt,
                actor=actor,
                message=None,
                payload=None,
                created_at=now,
            )
        return self.get_node(node_id)

    def append_node_event(
        self,
        node_id: str,
        *,
        event_type: str,
        actor: str,
        message: str | None,
        payload: dict[str, object] | None,
    ) -> EvaluationNodeEvent:
        """向运行节点追加一条白名单执行事件并返回持久化快照。

        Args:
            node_id: 接收事件的运行节点标识。
            event_type: 上游已经筛选后的稳定事件类型。
            actor: 产生事件的组件，例如 benchmark 或 codex。
            message: 面向人的简短事件摘要，可为空。
            payload: 可审计的结构化白名单字段，可为空。

        Returns:
            带数据库自增标识和创建时间的不可变节点事件。

        Raises:
            ValueError: 事件类型或事件来源为空白字符串。
            TaskStateError: 节点不是运行态，不能继续写入过程事件。
        """
        if not event_type.strip():
            raise ValueError("event_type must not be blank")
        if not actor.strip():
            raise ValueError("actor must not be blank")

        # 过程事件不改变节点状态，仅记录其发生时所处的尝试次数。
        now = utc_now().isoformat()
        with self._connection() as connection:
            node = self._require_node_status(connection, node_id, {"running"}, "append event")
            event_id = self._insert_node_event(
                connection,
                task_id=str(node["task_id"]),
                node_id=node_id,
                event_type=event_type.strip(),
                from_status=None,
                to_status=None,
                attempt=int(node["attempt_count"]),
                actor=actor.strip(),
                message=message,
                payload=payload,
                created_at=now,
            )
            # 在同一事务中回读，确保调用方拿到的就是本次追加事件而非竞态结果。
            row = connection.execute(
                """
                SELECT id, task_id, node_id, event_type, from_status, to_status,
                       attempt, actor, message, payload_json, created_at
                FROM evaluation_node_events
                WHERE id = ?
                """,
                (event_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"appended node event disappeared: {event_id}")
        return self._row_to_node_event(row)

    def update_node_progress(
        self,
        node_id: str,
        *,
        completed: int,
        total: int,
    ) -> EvaluationNode:
        """更新运行节点的真实样本分母和已完成数量。"""
        if completed < 0 or total < 0 or completed > total:
            raise ValueError("progress must satisfy 0 <= completed <= total")
        now = utc_now().isoformat()
        checkpoint = {"completed_samples": completed, "total_samples": total}
        with self._connection() as connection:
            self._require_node_status(connection, node_id, {"running"}, "update progress")
            connection.execute(
                """
                UPDATE evaluation_nodes
                SET completed_samples = ?, total_samples = ?, checkpoint_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (completed, total, self._json(checkpoint), now, node_id),
            )
        return self.get_node(node_id)

    def reschedule_node(
        self,
        node_id: str,
        error_type: str,
        message: str,
    ) -> EvaluationNode:
        """记录瞬时错误并把未耗尽尝试次数的运行节点放回待执行态。"""
        now_dt = utc_now()
        now = now_dt.isoformat()
        with self._connection() as connection:
            row = self._require_node_status(connection, node_id, {"running"}, "reschedule")
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                raise TaskStateError("cannot reschedule node after max attempts")
            elapsed_ms = int(row["elapsed_ms"]) + self._attempt_elapsed_ms(row, now_dt)
            connection.execute(
                """
                UPDATE evaluation_nodes
                SET status = 'pending', error_type = ?, error_message = ?,
                    attempt_started_at = NULL, elapsed_ms = ?, updated_at = ?
                WHERE id = ?
                """,
                (error_type, message, elapsed_ms, now, node_id),
            )
            self._insert_node_event(
                connection,
                task_id=str(row["task_id"]),
                node_id=node_id,
                event_type="node_retry_scheduled",
                from_status="running",
                to_status="pending",
                attempt=int(row["attempt_count"]),
                actor="worker",
                message=message,
                payload={"error_type": error_type},
                created_at=now,
            )
        return self.get_node(node_id)

    def complete_node(
        self,
        node_id: str,
        output: dict[str, object],
        *,
        actor: str = "worker",
    ) -> EvaluationNode:
        """提交节点成功产物并在同一事务写入成功事件。"""
        now_dt = utc_now()
        now = now_dt.isoformat()
        with self._connection() as connection:
            row = self._require_node_status(connection, node_id, {"running"}, "complete")
            elapsed_ms = int(row["elapsed_ms"]) + self._attempt_elapsed_ms(row, now_dt)
            payload = {"output": output}
            connection.execute(
                """
                UPDATE evaluation_nodes
                SET status = 'success', output_json = ?, checkpoint_json = checkpoint_json,
                    finished_at = ?, attempt_started_at = NULL, elapsed_ms = ?, updated_at = ?
                WHERE id = ?
                """,
                (self._json(output), now, elapsed_ms, now, node_id),
            )
            self._insert_node_event(
                connection,
                task_id=str(row["task_id"]),
                node_id=node_id,
                event_type="node_succeeded",
                from_status="running",
                to_status="success",
                attempt=int(row["attempt_count"]),
                actor=actor,
                message=None,
                payload=payload,
                created_at=now,
            )
        return self.get_node(node_id)

    def fail_node(
        self,
        node_id: str,
        error_type: str,
        message: str,
        *,
        actor: str = "worker",
    ) -> EvaluationNode:
        """把运行节点转换为失败终态并保存错误分类。"""
        return self._finish_node(
            node_id,
            target="failed",
            event_type="node_failed",
            error_type=error_type,
            message=message,
            actor=actor,
        )

    def block_node(
        self,
        node_id: str,
        error_type: str,
        message: str,
        *,
        actor: str = "worker",
    ) -> EvaluationNode:
        """把运行节点转换为需要人工处理的阻塞终态。"""
        return self._finish_node(
            node_id,
            target="blocked",
            event_type="node_blocked",
            error_type=error_type,
            message=message,
            actor=actor,
        )

    def retry_node(
        self,
        task_id: str,
        node_id: str,
        *,
        actor: str = "local_user",
    ) -> EvaluationNode:
        """重置失败节点及其全部后继，并保留 Benchmark 样本检查点。"""
        now = utc_now().isoformat()
        with self._connection() as connection:
            selected = self._require_node(connection, node_id)
            if str(selected["task_id"]) != task_id:
                raise TaskNotFoundError(f"node not found: {node_id}")
            if str(selected["status"]) not in {"failed", "blocked"}:
                raise TaskStateError(f"cannot retry {selected['status']} node")

            rows = connection.execute(
                f"SELECT {_NODE_COLUMNS} FROM evaluation_nodes "  # noqa: S608
                "WHERE task_id = ? ORDER BY rowid ASC",
                (task_id,),
            ).fetchall()
            descendants = {str(selected["node_key"])}
            changed = True
            while changed:
                changed = False
                for row in rows:
                    dependencies = set(json.loads(str(row["depends_on_json"])))
                    key = str(row["node_key"])
                    if key not in descendants and dependencies & descendants:
                        descendants.add(key)
                        changed = True

            for row in rows:
                if str(row["node_key"]) not in descendants:
                    continue
                if str(row["status"]) == "running":
                    raise TaskStateError("cannot retry workflow while a descendant is running")
                preserve_checkpoint = str(row["kind"]) == "benchmark"
                connection.execute(
                    """
                    UPDATE evaluation_nodes
                    SET status = 'pending', attempt_count = 0, output_json = NULL,
                        checkpoint_json = CASE WHEN ? THEN checkpoint_json ELSE NULL END,
                        completed_samples = CASE WHEN ? THEN completed_samples ELSE 0 END,
                        total_samples = CASE WHEN ? THEN total_samples ELSE 0 END,
                        error_type = NULL, error_message = NULL,
                        attempt_started_at = NULL, finished_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        preserve_checkpoint,
                        preserve_checkpoint,
                        preserve_checkpoint,
                        now,
                        str(row["id"]),
                    ),
                )
                self._insert_node_event(
                    connection,
                    task_id=task_id,
                    node_id=str(row["id"]),
                    event_type="node_retried",
                    from_status=row["status"],
                    to_status="pending",
                    attempt=int(row["attempt_count"]),
                    actor=actor,
                    message=None,
                    payload={"requested_node_id": node_id},
                    created_at=now,
                )
        return self.get_node(node_id)

    def recover_running_nodes(self) -> int:
        """恢复服务异常停止时遗留的运行节点并返回处理数量。"""
        now_dt = utc_now()
        now = now_dt.isoformat()
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {_NODE_COLUMNS} FROM evaluation_nodes "  # noqa: S608
                "WHERE status = 'running' ORDER BY rowid ASC"
            ).fetchall()
            for row in rows:
                has_attempt = int(row["attempt_count"]) < int(row["max_attempts"])
                target: NodeStatus = "pending" if has_attempt else "failed"
                event_type = "node_recovered" if has_attempt else "node_failed"
                elapsed_ms = int(row["elapsed_ms"]) + self._attempt_elapsed_ms(row, now_dt)
                connection.execute(
                    """
                    UPDATE evaluation_nodes
                    SET status = ?, attempt_started_at = NULL, elapsed_ms = ?,
                        error_type = 'worker_interrupted',
                        error_message = '服务重启导致节点执行中断',
                        finished_at = CASE WHEN ? = 'failed' THEN ? ELSE NULL END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (target, elapsed_ms, target, now, now, str(row["id"])),
                )
                self._insert_node_event(
                    connection,
                    task_id=str(row["task_id"]),
                    node_id=str(row["id"]),
                    event_type=event_type,
                    from_status="running",
                    to_status=target,
                    attempt=int(row["attempt_count"]),
                    actor="system",
                    message="服务重启导致节点执行中断",
                    payload={"error_type": "worker_interrupted"},
                    created_at=now,
                )
        return len(rows)

    def cancel_nodes(self, task_id: str, *, actor: str = "local_user") -> int:
        """保留成功节点并把任务中其他未完成节点统一标记为取消。"""
        now_dt = utc_now()
        now = now_dt.isoformat()
        with self._connection() as connection:
            self._require_task(connection, task_id)
            rows = connection.execute(
                f"SELECT {_NODE_COLUMNS} FROM evaluation_nodes "  # noqa: S608
                "WHERE task_id = ? AND status NOT IN ('success', 'canceled')",
                (task_id,),
            ).fetchall()
            for row in rows:
                elapsed_ms = int(row["elapsed_ms"])
                if str(row["status"]) == "running":
                    elapsed_ms += self._attempt_elapsed_ms(row, now_dt)
                connection.execute(
                    """
                    UPDATE evaluation_nodes
                    SET status = 'canceled', attempt_started_at = NULL,
                        finished_at = ?, elapsed_ms = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, elapsed_ms, now, str(row["id"])),
                )
                self._insert_node_event(
                    connection,
                    task_id=task_id,
                    node_id=str(row["id"]),
                    event_type="node_canceled",
                    from_status=row["status"],
                    to_status="canceled",
                    attempt=int(row["attempt_count"]),
                    actor=actor,
                    message="评测任务已取消",
                    payload=None,
                    created_at=now,
                )
        return len(rows)

    def list_node_events(self, node_id: str) -> list[EvaluationNodeEvent]:
        """按写入顺序返回节点的追加式审计事件。"""
        with self._connection() as connection:
            self._require_node(connection, node_id)
            rows = connection.execute(
                """
                SELECT id, task_id, node_id, event_type, from_status, to_status,
                       attempt, actor, message, payload_json, created_at
                FROM evaluation_node_events
                WHERE node_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (node_id,),
            ).fetchall()
        return [self._row_to_node_event(row) for row in rows]

    def record_sample(
        self,
        node_id: str,
        sample: EvaluationSampleCheckpoint,
        *,
        completed: int,
        total: int,
    ) -> EvaluationSampleCheckpoint:
        """原子提交样本快照、节点检查点和审计事件。"""
        if sample.node_id != node_id:
            raise ValueError("sample node_id does not match target node")
        if completed < 0 or total < 0 or completed > total:
            raise ValueError("progress must satisfy 0 <= completed <= total")
        if sample.sample_index < 0:
            raise ValueError("sample_index must be non-negative")

        now = utc_now().isoformat()
        checkpoint = {"completed_samples": completed, "total_samples": total}
        with self._connection() as connection:
            node = self._require_node_status(connection, node_id, {"running"}, "record sample")
            connection.execute(
                """
                INSERT INTO evaluation_sample_results (
                    task_id, node_id, sample_key, sample_index, status, attempt_count,
                    input_json, result_json, last_error_json, created_at, updated_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id, sample_key) DO UPDATE SET
                    sample_index = excluded.sample_index,
                    status = excluded.status,
                    attempt_count = excluded.attempt_count,
                    input_json = excluded.input_json,
                    result_json = excluded.result_json,
                    last_error_json = excluded.last_error_json,
                    updated_at = excluded.updated_at,
                    finished_at = excluded.finished_at
                """,
                (
                    str(node["task_id"]),
                    node_id,
                    sample.sample_key,
                    sample.sample_index,
                    sample.status,
                    sample.attempt_count,
                    self._json(sample.input),
                    self._json(sample.result) if sample.result is not None else None,
                    self._json(sample.last_error) if sample.last_error is not None else None,
                    now,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE evaluation_nodes
                SET completed_samples = ?, total_samples = ?, checkpoint_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (completed, total, self._json(checkpoint), now, node_id),
            )
            self._insert_node_event(
                connection,
                task_id=str(node["task_id"]),
                node_id=node_id,
                event_type="sample_checkpointed",
                from_status="running",
                to_status="running",
                attempt=int(node["attempt_count"]),
                actor="worker",
                message=None,
                payload={"sample_key": sample.sample_key, **checkpoint},
                created_at=now,
            )
        return self._get_sample(node_id, sample.sample_key)

    def successful_sample_keys(self, node_id: str) -> set[str]:
        """返回节点中已经成功提交、恢复时必须跳过的样本键。"""
        with self._connection() as connection:
            self._require_node(connection, node_id)
            rows = connection.execute(
                """
                SELECT sample_key FROM evaluation_sample_results
                WHERE node_id = ? AND status = 'success'
                """,
                (node_id,),
            ).fetchall()
        return {str(row["sample_key"]) for row in rows}

    def list_samples(
        self,
        node_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
    ) -> EvaluationSamplePage:
        """按样本索引和键稳定分页读取节点样本快照。"""
        if not 1 <= limit <= 200:
            raise ValueError("sample page limit must be between 1 and 200")
        if status not in {None, "success", "failed"}:
            raise ValueError("sample status must be success or failed")
        cursor_index, cursor_key = self._decode_sample_cursor(cursor)
        clauses = ["node_id = ?"]
        values: list[object] = [node_id]
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        if cursor is not None:
            clauses.append("(sample_index > ? OR (sample_index = ? AND sample_key > ?))")
            values.extend((cursor_index, cursor_index, cursor_key))
        values.append(limit + 1)

        with self._connection() as connection:
            self._require_node(connection, node_id)
            rows = connection.execute(
                """
                SELECT task_id, node_id, sample_key, sample_index, status, attempt_count,
                       input_json, result_json, last_error_json,
                       created_at, updated_at, finished_at
                FROM evaluation_sample_results
                WHERE """
                + " AND ".join(clauses)
                + " ORDER BY sample_index ASC, sample_key ASC LIMIT ?",
                tuple(values),
            ).fetchall()

        has_more = len(rows) > limit
        selected = rows[:limit]
        items = tuple(self._row_to_sample(row) for row in selected)
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = f"{int(last['sample_index'])}:{last['sample_key']}"
        return EvaluationSamplePage(items=items, next_cursor=next_cursor)

    def get(self, task_id: str) -> EvaluationTask:
        """读取包含完整结果正文的任务详情。

        Args:
            task_id: 需要读取的稳定任务标识。

        Returns:
            从数据库恢复的完整任务快照。

        Raises:
            TaskNotFoundError: 数据库中不存在对应任务。
        """
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {_TASK_COLUMNS} FROM evaluation_tasks WHERE id = ?",  # noqa: S608
                (task_id,),
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        return self._row_to_task(row)

    def list(self) -> list[EvaluationTask]:
        """按创建时间倒序返回不含完整结果正文的任务摘要记录。"""
        # ID 作为同时间戳下的稳定次级排序，确保刷新时列表顺序不会跳动。
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM evaluation_tasks "  # noqa: S608
                "ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_pending(self) -> list[EvaluationTask]:
        """按创建先后返回等待重新入队的任务。"""
        # FIFO 恢复必须使用升序，避免服务重启后后创建的任务抢先执行。
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM evaluation_tasks "  # noqa: S608
                "WHERE status = 'pending' ORDER BY created_at ASC, rowid ASC"
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_resumable(self) -> list[EvaluationTask]:
        """按创建顺序返回排队中或可从节点检查点恢复的运行任务。"""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM evaluation_tasks "  # noqa: S608
                "WHERE status IN ('pending', 'running') ORDER BY created_at ASC, rowid ASC"
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def reopen_for_retry(self, task_id: str) -> EvaluationTask:
        """把失败顶层任务恢复为排队态，同时保留资源和节点审计历史。"""
        now = utc_now().isoformat()
        with self._connection() as connection:
            self._require_status(connection, task_id, {"failed"}, "retry")
            connection.execute(
                """
                UPDATE evaluation_tasks
                SET status = 'pending', finished_at = NULL, error_message = NULL,
                    benchmark = NULL, passed_samples = NULL, average_score = NULL,
                    result_json = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, task_id),
            )
        return self.get(task_id)

    def mark_running(self, task_id: str) -> EvaluationTask:
        """把排队任务切换为运行态并记录开始时间。

        Raises:
            TaskNotFoundError: 任务不存在。
            TaskStateError: 任务不处于 ``pending`` 状态。
        """
        now = utc_now().isoformat()
        self._update_status(
            task_id,
            expected={"pending"},
            target="running",
            assignments="started_at = ?, updated_at = ?",
            values=(now, now),
        )
        return self.get(task_id)

    def update_progress(self, task_id: str, *, completed: int, total: int) -> EvaluationTask:
        """更新运行中任务的真实样本完成数量。

        Args:
            task_id: 当前运行任务标识。
            completed: 已经生成样本级结果的数量。
            total: 本次实际加载的样本总数。

        Raises:
            ValueError: 数量为负或完成数量超过总数。
            TaskStateError: 任务不处于运行态。
        """
        if completed < 0 or total < 0 or completed > total:
            raise ValueError("progress must satisfy 0 <= completed <= total")
        now = utc_now().isoformat()
        # 来源状态写进 UPDATE 条件，使取消与进度竞争时只有先提交的一方生效。
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE evaluation_tasks
                SET completed_samples = ?, total_samples = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (completed, total, now, task_id),
            )
            self._require_updated_task(
                connection,
                cursor,
                task_id=task_id,
                expected={"running"},
                action="update progress",
            )
        return self.get(task_id)

    def update_resources(self, task_id: str, usage: ResourceUsage) -> EvaluationTask:
        """保存运行任务的当前资源读数并维护生命周期峰值。

        Args:
            task_id: 当前运行任务标识。
            usage: 同一次采样得到的 CPU、内存与可选 GPU 数据。

        Raises:
            TaskStateError: 任务不处于运行态。
        """
        now = utc_now().isoformat()
        # GPU 空值保留“不支持或无可靠读数”的语义，不能用零替代。
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE evaluation_tasks
                SET cpu_percent = ?, peak_cpu_percent = MAX(peak_cpu_percent, ?),
                    memory_bytes = ?, peak_memory_bytes = MAX(peak_memory_bytes, ?),
                    gpu_supported = MAX(gpu_supported, ?), gpu_percent = ?,
                    peak_gpu_percent = CASE
                        WHEN ? IS NULL THEN peak_gpu_percent
                        ELSE MAX(COALESCE(peak_gpu_percent, 0), ?)
                    END,
                    gpu_memory_bytes = ?, peak_gpu_memory_bytes = CASE
                        WHEN ? IS NULL THEN peak_gpu_memory_bytes
                        ELSE MAX(COALESCE(peak_gpu_memory_bytes, 0), ?)
                    END,
                    updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    usage.cpu_percent,
                    usage.cpu_percent,
                    usage.memory_bytes,
                    usage.memory_bytes,
                    int(usage.gpu_supported),
                    usage.gpu_percent,
                    usage.gpu_percent,
                    usage.gpu_percent,
                    usage.gpu_memory_bytes,
                    usage.gpu_memory_bytes,
                    usage.gpu_memory_bytes,
                    now,
                    task_id,
                ),
            )
            self._require_updated_task(
                connection,
                cursor,
                task_id=task_id,
                expected={"running"},
                action="update resources",
            )
        return self.get(task_id)

    def mark_success(self, task_id: str, result: dict[str, object]) -> EvaluationTask:
        """保存完整评测结果并把运行任务转换为成功终态。

        Args:
            task_id: 当前运行任务标识。
            result: 同步评测核心生成的完整 JSON 兼容结果。

        Raises:
            TaskStateError: 任务不处于运行态。
        """
        finished_at = utc_now().isoformat()
        total = int(result["total_samples"])
        passed = int(result["passed_samples"])
        # 结果摘要拆列支持轻量列表查询，正文仍以 JSON 保留兼容字段。
        values = (
            total,
            total,
            str(result["benchmark"]),
            passed,
            float(result["average_score"]),
            json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str),
            finished_at,
            finished_at,
        )
        self._update_status(
            task_id,
            expected={"running"},
            target="success",
            assignments=(
                "completed_samples = ?, total_samples = ?, benchmark = ?, "
                "passed_samples = ?, average_score = ?, result_json = ?, "
                "finished_at = ?, updated_at = ?"
            ),
            values=values,
        )
        return self.get(task_id)

    def mark_failed(self, task_id: str, message: str) -> EvaluationTask:
        """保存失败原因并把排队或运行任务转换为失败终态。"""
        finished_at = utc_now().isoformat()
        self._update_status(
            task_id,
            expected={"pending", "running"},
            target="failed",
            assignments="error_message = ?, finished_at = ?, updated_at = ?",
            values=(message, finished_at, finished_at),
        )
        return self.get(task_id)

    def mark_canceled(self, task_id: str) -> EvaluationTask:
        """把排队或运行任务转换为取消终态并保留已有进度。"""
        finished_at = utc_now().isoformat()
        self._update_status(
            task_id,
            expected={"pending", "running"},
            target="canceled",
            assignments="finished_at = ?, updated_at = ?",
            values=(finished_at, finished_at),
        )
        return self.get(task_id)

    def recover_interrupted(self, message: str) -> int:
        """把服务启动时遗留的运行任务标记为失败。

        Args:
            message: 向用户解释非正常终止原因的安全文本。

        Returns:
            实际从运行态转换为失败态的任务数量。
        """
        finished_at = utc_now().isoformat()
        # 单条 SQL 原子处理全部遗留运行态，避免部分恢复后再次崩溃产生歧义。
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE evaluation_tasks
                SET status = 'failed', error_message = ?, finished_at = ?, updated_at = ?
                WHERE status = 'running'
                """,
                (message, finished_at, finished_at),
            )
        return cursor.rowcount

    def _update_status(
        self,
        task_id: str,
        *,
        expected: set[str],
        target: TaskStatus,
        assignments: str,
        values: tuple[object, ...],
    ) -> None:
        """在同一事务内校验来源状态并完成目标状态更新。

        Args:
            task_id: 需要转换的任务标识。
            expected: 允许进入本次转换的来源状态集合。
            target: 要写入的目标状态。
            assignments: 除状态外的受控 SQL 赋值片段。
            values: 与赋值片段占位符顺序一致的参数。
        """
        expected_statuses = sorted(expected)
        placeholders = ", ".join("?" for _ in expected_statuses)
        with self._connection() as connection:
            # 来源状态直接进入原子更新条件，终态竞争不能在校验后互相覆盖。
            sql = (
                f"UPDATE evaluation_tasks SET status = ?, {assignments} "
                f"WHERE id = ? AND status IN ({placeholders})"
            )
            cursor = connection.execute(
                sql,
                (target, *values, task_id, *expected_statuses),
            )
            self._require_updated_task(
                connection,
                cursor,
                task_id=task_id,
                expected=expected,
                action=f"transition to {target}",
            )

    def _require_updated_task(
        self,
        connection: sqlite3.Connection,
        cursor: sqlite3.Cursor,
        *,
        task_id: str,
        expected: set[str],
        action: str,
    ) -> None:
        """确认条件更新命中任务，否则转换为缺失或状态冲突异常。

        Args:
            connection: 执行条件更新的同一事务连接。
            cursor: 条件 UPDATE 返回的游标。
            task_id: 目标任务标识。
            expected: UPDATE 允许的来源状态。
            action: 用于状态冲突诊断的操作说明。

        Raises:
            TaskNotFoundError: 任务不存在。
            TaskStateError: 并发更新已使任务离开允许状态。
        """
        if cursor.rowcount == 1:
            return
        # 同事务复读把“零行更新”细分为不存在或并发状态冲突，保持 API 错误稳定。
        self._require_status(connection, task_id, expected, action)
        raise TaskStateError(f"task could not {action}")

    def _require_status(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        expected: set[str],
        action: str,
    ) -> str:
        """读取任务当前状态并验证指定操作是否允许继续。

        Raises:
            TaskNotFoundError: 任务不存在。
            TaskStateError: 当前状态不在允许集合内。
        """
        row = connection.execute(
            "SELECT status FROM evaluation_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        current = str(row["status"])
        if current not in expected:
            # 终态转换错误采用稳定措辞，HTTP 层和测试均可直接给出诊断信息。
            target = action.removeprefix("transition to ")
            raise TaskStateError(f"cannot transition {current} task to {target}")
        return current

    def _finish_node(
        self,
        node_id: str,
        *,
        target: NodeStatus,
        event_type: str,
        error_type: str,
        message: str,
        actor: str,
    ) -> EvaluationNode:
        """完成运行节点的错误终态转换并原子追加分类事件。"""
        now_dt = utc_now()
        now = now_dt.isoformat()
        with self._connection() as connection:
            row = self._require_node_status(connection, node_id, {"running"}, target)
            elapsed_ms = int(row["elapsed_ms"]) + self._attempt_elapsed_ms(row, now_dt)
            connection.execute(
                """
                UPDATE evaluation_nodes
                SET status = ?, error_type = ?, error_message = ?,
                    attempt_started_at = NULL, finished_at = ?, elapsed_ms = ?, updated_at = ?
                WHERE id = ?
                """,
                (target, error_type, message, now, elapsed_ms, now, node_id),
            )
            self._insert_node_event(
                connection,
                task_id=str(row["task_id"]),
                node_id=node_id,
                event_type=event_type,
                from_status="running",
                to_status=target,
                attempt=int(row["attempt_count"]),
                actor=actor,
                message=message,
                payload={"error_type": error_type},
                created_at=now,
            )
        return self.get_node(node_id)

    @staticmethod
    def _require_task(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        """读取任务基础行，不存在时抛出稳定仓储异常。"""
        row = connection.execute(
            "SELECT id, status FROM evaluation_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        return row

    @staticmethod
    def _require_node(connection: sqlite3.Connection, node_id: str) -> sqlite3.Row:
        """读取节点基础行，不存在时抛出稳定仓储异常。"""
        row = connection.execute(
            f"SELECT {_NODE_COLUMNS} FROM evaluation_nodes WHERE id = ?",  # noqa: S608
            (node_id,),
        ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"node not found: {node_id}")
        return row

    def _require_node_status(
        self,
        connection: sqlite3.Connection,
        node_id: str,
        expected: set[str],
        action: str,
    ) -> sqlite3.Row:
        """读取节点并验证当前状态允许指定操作。"""
        row = self._require_node(connection, node_id)
        current = str(row["status"])
        if current not in expected:
            raise TaskStateError(f"cannot {action} {current} node")
        return row

    def _insert_node_event(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        node_id: str,
        event_type: str,
        from_status: NodeStatus | None,
        to_status: NodeStatus | None,
        attempt: int,
        actor: str,
        message: str | None,
        payload: dict[str, object] | None,
        created_at: str,
    ) -> int:
        """使用调用方事务追加一条节点审计事件并返回数据库标识。"""
        cursor = connection.execute(
            """
            INSERT INTO evaluation_node_events (
                task_id, node_id, event_type, from_status, to_status,
                attempt, actor, message, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                node_id,
                event_type,
                from_status,
                to_status,
                attempt,
                actor,
                message,
                self._json(payload) if payload is not None else None,
                created_at,
            ),
        )
        return int(cursor.lastrowid)

    def _get_sample(self, node_id: str, sample_key: str) -> EvaluationSampleCheckpoint:
        """读取指定节点和样本键的最新检查点。"""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT task_id, node_id, sample_key, sample_index, status, attempt_count,
                       input_json, result_json, last_error_json,
                       created_at, updated_at, finished_at
                FROM evaluation_sample_results
                WHERE node_id = ? AND sample_key = ?
                """,
                (node_id, sample_key),
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"sample not found: {node_id}/{sample_key}")
        return self._row_to_sample(row)

    @staticmethod
    def _decode_sample_cursor(cursor: str | None) -> tuple[int, str]:
        """解析 ``sample_index:sample_key`` 格式的稳定分页游标。"""
        if cursor is None:
            return -1, ""
        try:
            index_text, sample_key = cursor.split(":", 1)
            index = int(index_text)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid sample cursor") from exc
        if index < 0 or not sample_key:
            raise ValueError("invalid sample cursor")
        return index, sample_key

    @staticmethod
    def _attempt_elapsed_ms(row: sqlite3.Row, finished_at: datetime) -> int:
        """计算当前节点尝试从开始到指定结束时间的非负毫秒数。"""
        value = row["attempt_started_at"]
        if value is None:
            return 0
        started_at = datetime.fromisoformat(str(value))
        return max(0, int((finished_at - started_at).total_seconds() * 1000))

    @staticmethod
    def _json(value: object) -> str:
        """使用统一参数把 Python 值序列化为紧凑 JSON。"""
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    def _row_to_node(self, row: sqlite3.Row) -> EvaluationNode:
        """把 SQLite 行转换为不可变节点快照。"""
        return EvaluationNode(
            id=str(row["id"]),
            task_id=str(row["task_id"]),
            node_key=str(row["node_key"]),
            kind=str(row["kind"]),
            depends_on=tuple(json.loads(str(row["depends_on_json"]))),
            status=row["status"],
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            input=json.loads(str(row["input_json"])),
            checkpoint=(
                json.loads(str(row["checkpoint_json"])) if row["checkpoint_json"] else None
            ),
            output=json.loads(str(row["output_json"])) if row["output_json"] else None,
            error_type=row["error_type"],
            error_message=row["error_message"],
            completed_samples=int(row["completed_samples"]),
            total_samples=int(row["total_samples"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            started_at=self._optional_datetime(row["started_at"]),
            attempt_started_at=self._optional_datetime(row["attempt_started_at"]),
            finished_at=self._optional_datetime(row["finished_at"]),
            elapsed_ms=int(row["elapsed_ms"]),
        )

    @staticmethod
    def _row_to_node_event(row: sqlite3.Row) -> EvaluationNodeEvent:
        """把 SQLite 行转换为追加式节点事件。"""
        return EvaluationNodeEvent(
            id=int(row["id"]),
            task_id=str(row["task_id"]),
            node_id=str(row["node_id"]),
            event_type=str(row["event_type"]),
            from_status=row["from_status"],
            to_status=row["to_status"],
            attempt=int(row["attempt"]),
            actor=str(row["actor"]),
            message=row["message"],
            payload=(json.loads(str(row["payload_json"])) if row["payload_json"] else None),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _row_to_sample(row: sqlite3.Row) -> EvaluationSampleCheckpoint:
        """把 SQLite 行转换为样本检查点。"""
        return EvaluationSampleCheckpoint(
            task_id=str(row["task_id"]),
            node_id=str(row["node_id"]),
            sample_key=str(row["sample_key"]),
            sample_index=int(row["sample_index"]),
            status=row["status"],
            attempt_count=int(row["attempt_count"]),
            input=json.loads(str(row["input_json"])),
            result=json.loads(str(row["result_json"])) if row["result_json"] else None,
            last_error=(
                json.loads(str(row["last_error_json"])) if row["last_error_json"] else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            finished_at=SQLiteTaskRepository._optional_datetime(row["finished_at"]),
        )

    def _row_to_task(self, row: sqlite3.Row) -> EvaluationTask:
        """把 SQLite 行转换为带类型的不可变任务快照。"""
        request_payload = json.loads(str(row["request_json"]))
        result_payload = json.loads(str(row["result_json"])) if row["result_json"] else None
        # ISO 时间统一由 UTC 工具写入，读取时保留原始时区信息供耗时计算。
        return EvaluationTask(
            id=str(row["id"]),
            request=TaskRequest(**request_payload),
            status=row["status"],
            completed_samples=int(row["completed_samples"]),
            total_samples=int(row["total_samples"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            started_at=self._optional_datetime(row["started_at"]),
            finished_at=self._optional_datetime(row["finished_at"]),
            error_message=row["error_message"],
            cpu_percent=float(row["cpu_percent"]),
            peak_cpu_percent=float(row["peak_cpu_percent"]),
            memory_bytes=int(row["memory_bytes"]),
            peak_memory_bytes=int(row["peak_memory_bytes"]),
            gpu_supported=bool(row["gpu_supported"]),
            gpu_percent=self._optional_float(row["gpu_percent"]),
            peak_gpu_percent=self._optional_float(row["peak_gpu_percent"]),
            gpu_memory_bytes=self._optional_int(row["gpu_memory_bytes"]),
            peak_gpu_memory_bytes=self._optional_int(row["peak_gpu_memory_bytes"]),
            benchmark=row["benchmark"],
            passed_samples=self._optional_int(row["passed_samples"]),
            average_score=self._optional_float(row["average_score"]),
            result=result_payload,
        )

    @staticmethod
    def _optional_datetime(value: object) -> datetime | None:
        """把 SQLite 可空 ISO 时间转换为带时区的 datetime。"""
        return datetime.fromisoformat(str(value)) if value is not None else None

    @staticmethod
    def _optional_float(value: object) -> float | None:
        """把 SQLite 可空数值转换为浮点数并保留空值语义。"""
        return float(value) if value is not None else None

    @staticmethod
    def _optional_int(value: object) -> int | None:
        """把 SQLite 可空整数转换为 Python 整数并保留空值语义。"""
        return int(value) if value is not None else None
