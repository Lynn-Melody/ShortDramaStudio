# n8n 开发目录

将可导入的工作流 JSON 放在这里。Compose 将此目录只读挂载到 n8n 容器的 `/workflows`。在编辑器中完成本地账号初始化后，可以用界面的文件导入功能，或用 `docker compose exec n8n n8n import:workflow --input=/workflows/<文件名>.json` 导入。

工作流容器可通过 `http://api:8000`、`http://agents:8010` 和 `http://render:8020` 访问相应服务。平台派发和回调见[工作流接入](../../docs/workflow-integration.md)。

## 全剧规划联调

1. 从 `.env.example` 准备 `.env`。设置独立的 `STUDIO_WORKFLOW_SERVICE_TOKEN`、`STUDIO_WORKFLOW_VERIFIER_TOKEN` 和 `STUDIO_N8N_DISPATCH_TOKEN`。启动 Compose 并在 n8n 编辑器中初始化账号。
2. 导入 [t03-series-plan-smoke.json](t03-series-plan-smoke.json)。创建两个 Header Auth 凭据。给 Dispatch 节点绑定 `X-Studio-Dispatch-Token`，其值为 Worker 的派发机密。给 Accept 和 Submit Result 节点绑定 `Authorization`，其值为 `Bearer <STUDIO_WORKFLOW_SERVICE_TOKEN>`。保存并发布工作流。 [n8n Webhook 官方文档](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/)
3. 在 `.env` 中设置 `STUDIO_N8N_WEBHOOKS='{"series_plan":"http://n8n:5678/webhook/t03-series-plan-smoke"}'`，运行 `docker compose up -d worker` 使配置生效。
4. 通过 Django shell 创建并启动项目。用返回的项目 ID 在 n8n 执行记录和 Django 数据库中核验，预期规划任务完成、项目名称为 `Night Service`、新增一集 `Last Train`，并创建 `global_assets` 任务。样例只提供全剧规划的 Webhook。

```powershell
docker compose exec api python apps/backend/manage.py shell -c "from production.services import create_project; from scheduler.worker import configured_scheduler; project, _ = create_project('A ghost train returns.'); configured_scheduler().set_enabled(project.pk, True); print(project.pk)"
```
