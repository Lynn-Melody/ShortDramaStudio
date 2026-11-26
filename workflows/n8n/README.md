# n8n 开发目录

将可导入的工作流 JSON 放在这里。Compose 将此目录只读挂载到 n8n 容器的 `/workflows`。在编辑器中完成本地账号初始化后，可以用界面的文件导入功能，或用 `docker compose exec n8n n8n import:workflow --input=/workflows/<文件名>.json` 导入。

工作流容器可通过 `http://api:8000`、`http://agents:8010` 和 `http://render:8020` 访问相应服务。当前服务提供开发环境健康检查，生产任务与工作流按[技术设计](../../docs/technical-design.md)逐步接入。
