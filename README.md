# 短剧工作室

本项目按[技术设计](docs/technical-design.md)搭建短剧生产平台。[生产流程](docs/production-flow.md)定义业务顺序，[产品设计](docs/product-design.md)描述工作台交互。

## 启动开发环境

安装支持 Linux 容器的 Docker 和 Docker Compose v2 或更新版本。在仓库根目录运行：

```powershell
docker compose up --build --wait
docker compose ps
```

需要覆盖本地配置时，将 `.env.example` 复制为 `.env` 后再启动。Compose 默认使用开发凭据，对宿主机发布的端口只绑定到 `127.0.0.1`。

| 入口 | 地址 |
| --- | --- |
| React 工作台 | http://127.0.0.1:5173 |
| Django API | http://127.0.0.1:8000/api/v1/health |
| Django Admin | http://127.0.0.1:8000/admin/ |
| n8n 编辑器 | http://127.0.0.1:5678 |
| MinIO 控制台 | http://127.0.0.1:9001 |
| PostgreSQL | 127.0.0.1:5432 |

首次启动会构建固定版本的 MinIO 社区版，创建平台和 n8n 各自的数据库与角色、运行 Django 迁移并初始化私有开发桶。用 `docker compose exec api python apps/backend/manage.py createsuperuser` 创建 Admin 账号。n8n 账号在编辑器中创建。Agent 和 FFmpeg 服务通过 Compose 内网访问。

当前工作台显示 API 连通状态。Worker、Agent、渲染服务分别提供启动检查或健康接口，供调度、角色和时间轴实现接入。[n8n 工作流目录](workflows/n8n/README.md)说明导入和内部服务地址。

`docker compose down` 停止服务并保留数据库、对象和 n8n 数据。数据库角色和密码在数据卷首次初始化时写入，已有卷上的角色密码需通过 PostgreSQL 更新。

## 本地安装与验证

本机开发使用 Python 3.14、uv、Node.js 24 和 pnpm 11.25.0。建议先启动 Compose，也可使用 `docker compose up -d --wait postgres` 单独启动测试数据库。

在仓库根目录执行后端、文档和原型检查。

```powershell
uv sync --locked
node --test docs/production-flow.test.cjs prototype/prototype.test.cjs
uv run --locked python apps/backend/manage.py check
uv run --locked pytest
```

在 `apps/web` 运行前端命令，让 Corepack 读取该目录的 pnpm 版本声明。已安装指定版本 pnpm 的环境可以直接运行这些 pnpm 命令。

```powershell
Set-Location apps/web
corepack enable
pnpm install --frozen-lockfile
pnpm types:check
pnpm test
pnpm build
pnpm exec playwright install chromium
pnpm test:e2e
```

Playwright 访问运行中的工作台和真实 Django API。如果只启动了 PostgreSQL，先在一个终端从仓库根目录运行 `uv run --locked python apps/backend/manage.py runserver`，再在另一终端进入 `apps/web` 运行 `pnpm dev`。本地数据库设置默认连接 Compose 的 `studio` 库。如修改了开发密码，请在本机后端或 pytest 进程中设置对应的 `STUDIO_DB_PASSWORD`。

修改后端 Serializer 或路由后，从仓库根目录生成两份 OpenAPI 3.1 契约，再进入 `apps/web` 生成前端类型。将生成的文件一起提交。

```powershell
uv run --locked python apps/backend/manage.py spectacular --urlconf studio.public_urls --file contracts/public.yaml --validate --fail-on-warn
uv run --locked python apps/backend/manage.py spectacular --urlconf studio.internal_urls --file contracts/internal.yaml --validate --fail-on-warn
Set-Location apps/web
pnpm types:generate
```
