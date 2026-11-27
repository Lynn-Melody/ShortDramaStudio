# T01 调度模块接入说明

调度模块以 PostgreSQL 保存任务图、执行记录和派发记录。业务任务使用 Python 类声明下一步，平台负责执行状态、等待、恢复和重试。完整业务规则见 [生产流程](production-flow.md)。

## 接入生产任务

T02 从 `scheduler.tasks` 使用 `BaseTask`、`Completed`、`Failed` 和 `RetryPolicy`。把具体类的模块路径加到 Django 设置 `SCHEDULER_TASK_CLASSES`。只要仍有任务执行或恢复使用某个版本，就保留对应的类注册。Worker 和 API 可通过 `scheduler.worker.configured_scheduler()` 使用同一份注册配置。独立测试可实例化 `Scheduler(TaskRegistry(...))`。

- 创建项目时，在业务事务内调用 `create_schedule(root_class, input, schedule_id=project_id)`。返回 `(Schedule, Task)`，根任务及其占位一同创建，初始暂停。相同项目 UUID、根任务类型、定义版本和输入的重放返回已有记录，输入或定义不一致时报 `StateConflict`。
- `on_init()` 从创建时的输入固定 `child_keys`、`child_total` 和 `checkpoint`。可用 `cur_step` 读写检查点中的 `step`，恢复时直接加载快照。父任务引用可用 `get_parent_task()` 读取。
- `on_processing()` 使用领取时的输入和产出快照。通过 `child_output(key)` 读取已完成的直属子任务产出，`dependency_outputs` 按前置任务 UUID 字符串索引。结果必须是 JSON 对象。初始化、运行决策和工作流结果映射应无副作用。
- `wait_for_children(children, wait_for, next_step)` 声明填入的子任务及本轮等待的直属位置。子任务依赖接受直属子任务键或同一项目的 `UUID`。等待时可以只引用已创建的位置。调度器统一写入，并校验依赖与父任务最终完成所需的子任务关系无环。父任务最终完成需要所有预留位置完成。
- 无需执行的任务可使用 `finish(reason="原因")`。参考 [通用任务示例](../apps/backend/scheduler/examples.py) 的串行、并行和条件直接完成。

## 接入工作流与素材

T03 将 `SCHEDULER_WORKFLOW_DISPATCHER` 配置为接收 `WorkflowRequest` 的函数路径。请求包含工作流名、项目及任务 ID、任务类型、定义版本、执行 ID 和截止时间。`idempotency_key` 在同一次执行内保持不变。请求体的业务输入由接收接口提供。

| 对接点 | 调用方式 |
| --- | --- |
| 工作流接收 | `accept_workflow(execution_id)` 返回 `WorkflowAcceptance`。仅首次 `accepted` 携带业务输入，其他状态用来结束重复、过期或未就绪的调用 |
| 执行有效性 | `workflow_is_current(execution_id)` 在已接收的工作流仍是当前执行且未到期时返回真 |
| 工作流结果 | `submit_workflow_result(execution_id, Completed(...) 或 Failed(...))`。结果先经任务类的 `on_workflow_result()` 映射，当前有效结果才可在项目锁内提交。映射异常或无效结果会将当前执行记为 `invalid_workflow_result`，等待人工处理 |
| 素材登记 | T03 和 T04 可以传入 `record_outputs(execution, output) -> dict`。函数仅写数据库并返回最终引用，与任务完成同事务。服务身份、上传与对象验证在调用前由接入层完成 |
| 供应商任务 | `register_provider_job(execution_id, step_key, provider, job_id)` 幂等登记，也接收仍是当前执行的超时工作流补报的作业 ID。已接收的工作流超时后，接入层核查工作流不会再发起请求、在途请求已查清且供应商作业无后续结果，再调用 `confirm_provider_jobs_inactive(execution_id, jobs=已核查的快照)` 恢复重试策略。登记列表为空时仍需核查工作流与在途请求 |
| 传输失败 | 派发函数用 `DeliveryError(code, retryable=True)` 表示可重传，同一执行退避重发。确定不可重试时传 `retryable=False` |

T03 在 HTTP 入口负责服务授权、关联任务与执行 ID、接口版本和请求字段校验。响应 `applied` 表示结果已入库，包括任务类判定的业务失败。重复回调返回 `already_processed`。已超时的有效成功结果只有仍对应当前执行时才可被接纳。素材登记异常会回滚本次写入，回调可使用同一执行 ID 重试提交。派发函数的网络请求应设置超时，并受 `deadline_at` 约束。工作流派发器尚未配置时，Worker 仍可执行本地任务。

## 接入工作台

T05 通过 `set_enabled(schedule_id, enabled)` 暂停或继续，首次启动时间在 `Schedule.started_at`。项目完成以根任务 `state` 为准。用 `retry(schedule_id, task_id)` 重试失败阻塞或已超时的任务，`StateConflict` 表示当前状态不允许操作。`provider_jobs_unverified` 表示接入层需核查工作流和供应商活动，核查完成后恢复重试。

读取任务和历史时按 `Task.schedule_id` 限定查询范围。`parent_id` 用于展示任务树，`dependencies` 与 `waits` 用于说明阻塞，`executions` 提供执行历史。执行的 `accepted` 表示本地 Worker 已接收，外部工作流接收时间使用 `workflow_accepted_at`。项目进度取根任务的 `progress`，其值是 0 到 1 的小数，在界面转换为百分比。收尾时进度可以先达到 100%，后续任务只在当前任务完成后才启动。

## 运行与验证

`manage.py run_scheduler` 持续轮询，`--once` 执行一轮领取与派发，`--check` 检查数据库连接。Worker 优先处理已登记的派发，按已注册的任务版本领取、派发及回收。单条派发的临时持久化故障可以等租约到期后重试，其他任务继续推进。调度器使用 PostgreSQL 行锁与会话级 advisory lock，Worker 应使用能保留数据库会话的连接。按 [仓库说明](../README.md) 启动数据库并运行迁移和测试。

调度测试在真实 PostgreSQL 上运行，覆盖多连接领取、暂停竞争、持久化派发、工作流重复回调、超时与重试竞争、等权进度和旧版本恢复。
