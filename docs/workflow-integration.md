# T03 工作流接入

接口定义见[内部 OpenAPI v1](../contracts/internal.yaml)，派发、领取、回调和供应商检查的请求样例见[工作流 v1 样例](../contracts/workflow-v1.examples.json)。具体叶子的输入和成功结果见[生产 v1 样例](../contracts/production-v1.examples.json)。

## Worker 派发

在 Worker 环境中设置 `STUDIO_N8N_WEBHOOKS`，值为工作流名到生产 Webhook 完整 URL 的 JSON 对象。例如 `{"series_plan":"http://n8n:5678/webhook/t03-series-plan-smoke"}`。工作流名见[生产接入](production-integration.md)。空配置时 Worker 仍处理本地任务，工作流派发保留在 outbox 中直至配置或执行过期。启用映射后，缺少某个工作流地址会记录 `workflow_webhook_missing`，配置修正后可显式重试该任务。

Worker 通过 POST 发送身份、任务类型及截止时间。稳定的 `Idempotency-Key` 就是 `executionId`。将 Worker 的 `STUDIO_N8N_DISPATCH_TOKEN` 配为 n8n Webhook 的 Header Auth 凭据，头名为 `X-Studio-Dispatch-Token`。同次执行在传输重试中保留原执行 ID 和截止时间。单次请求的连接与读取超时取剩余时间和 5 秒中的较小值。网络错误、HTTP 408、425、429 和 5xx 可退避重传，其他非 2xx 状态终止当次派发，重定向也视为派发失败。

n8n 的首个业务动作是调用平台 `accept`。取得接收状态后，通过 Respond to Webhook 响应派发。只有 `accepted` 带有不可变的 `input`，也只有这一状态可继续执行业务。重复派发获得的 `already_accepted` 或 `already_processed` 可直接结束。

## 平台内部接口

以 `/internal/v1/executions/{executionId}` 为公共路径。POST 和 PUT 在 JSON 体提供 `projectId`、`taskId`、`executionId`、`schemaVersion`。GET 将项目、任务及版本放在查询参数中，执行 ID 取路径。版本为字符串 `"1"`。身份格式或版本无效返回 400，路径执行与项目或任务无法匹配时返回 404。

API 环境设置 `STUDIO_WORKFLOW_SERVICE_TOKEN`，n8n 的出站 HTTP 节点使用 `Authorization: Bearer <token>`。独立核查身份使用 `STUDIO_WORKFLOW_VERIFIER_TOKEN`。在部署环境为两者及 Webhook 使用不同的机密，凭据通过运行环境或 n8n Credentials 注入。缺少凭据时对应入口拒绝请求。Compose 将 API 和 Worker 各自所需的配置从 `.env` 注入。

| 方法与路径 | 调用方式 |
| --- | --- |
| POST `/accept` | 原子领取并返回一次固定输入。其他状态为 `already_accepted`、`already_processed`、`expired`、`not_ready` |
| GET `/status` | 每次发起付费或耗时步骤前检查 `active`。仅当前、已接收且在截止时间内的执行返回 `true` |
| POST `/result` | `outcome: completed` 携带 `data` 和 `outputs`，`outcome: failed` 携带 `errorCode` 与 `message`。返回 `applied`、`already_processed`、`expired` 或 `not_accepted` |
| PUT `/provider-jobs/{stepKey}` | 取得供应商作业 ID 后立即登记。同一步骤重传相同供应商和 ID 返回 204，冲突返回 409。支持当前超时执行补报 |
| GET `/provider-check` | 用核查身份读取是否待核查及当前持久化作业快照 |
| POST `/provider-check` | 用核查身份提交已核查的完整快照。确认成功返回 204，记录变更或执行已不待核查返回 409 |

回调信封或产物描述格式错误返回 400。业务数据由生产叶子校验，调度器持久化叶子判定的 `invalid_workflow_result`，回调响应为 `applied`。当前执行超时后仍可采纳有效的晚到成功结果。合法的非空媒体输出进入 T04 登记边界，登记接通前返回 503，工作流可使用相同执行重试。结构化叶子以空 `outputs` 完成。

核查方先从 n8n 执行记录确认工作流已停止且无法发出新的供应商请求，再按执行及步骤关联键确认在途请求已查清、供应商作业不会再产出。只有三项均有证据时，才以 `workflowStopped`、`requestsSettled`、`jobsInactive` 均为 `true` 的请求提交实际核查的作业快照。空作业列表仍需完成前两项核查。证据不明时，执行保持待核查。晚到登记导致快照变化时，读取新快照并核查新增作业。
