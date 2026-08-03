# SQLite 单 Worker 锁设计

## 目标

同一个 SQLite 任务库任一时刻只允许一个 `EvaluationTaskService` Worker 运行，避免第二个
后端实例把正在执行的节点误判为重启恢复任务，并在退出时改写共享任务状态。

## 设计

- `EvaluationTaskService.start()` 在恢复任务前，针对数据库旁的固定锁文件获取非阻塞
  `fcntl.flock` 独占锁。
- 锁已被占用时立即抛出明确错误，不恢复节点、不启动线程，也不修改任务状态。
- Worker 确认退出后释放锁；启动中途失败时同样释放锁。若 Worker 在关闭超时后仍存活，
  保留锁，防止第二个 Worker 接管仍在执行的任务。
- 不改变 SQLite 表结构、HTTP API、任务恢复规则或任务结果格式。

## 验证

回归测试使用同一个临时数据库启动两个服务：第二个必须失败；第一个停止后，第二个必须能
正常启动。相关服务测试、完整 pytest、Ruff 和 `git diff --check` 均需通过。
