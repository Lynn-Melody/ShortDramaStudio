# T05 工作台 API 接入

工作台通过公开 REST 接口 `/api/v1` 读取和操作项目。完整字段见[公开 OpenAPI v1](../contracts/public.yaml)，前端类型由 `apps/web` 的 `pnpm types:generate` 生成到 `src/api.d.ts`。[公开 v1 响应样例](../contracts/public-v1.examples.json)来自两集、每集两镜的真实生产测试，并由后端测试按 Serializer 校验。产品交互见[产品设计](product-design.md)，调度语义见 [T01 调度接入说明](scheduler-integration.md)。

## 身份与请求

- 公开接口只接受 Django 会话。工作流和核查服务的 Bearer 凭据在这里无效，工作台会话也不能调用 `/internal/v1`。首版单工作室共用项目，任何已登录的有效账号都可读取和操作全部项目。
- 页面启动时调用 `GET /api/v1/session`，取得登录状态和 `csrfToken`，并写入 `csrftoken` Cookie。登录用 `POST /api/v1/session` 提交 `username`、`password`，退出用 `DELETE /api/v1/session`。所有 POST 和 DELETE 都带请求头 `X-CSRFToken`，登录会轮换令牌，之后使用登录响应中的新 `csrfToken`。开发账号用 `createsuperuser` 创建，生产入口由部署环境的统一身份认证接入。关闭 `DEBUG` 的部署须用 `STUDIO_CSRF_TRUSTED_ORIGINS` 列出工作台来源，开发默认信任 `http://127.0.0.1:5173` 和 `http://localhost:5173`。
- 未登录或缺少 CSRF 令牌返回 403。路径里的项目、任务、素材或版本不属于该项目时返回 404；查询参数中的 `episodeId` 或 `shotId` 不属于该项目时返回 400。400 的响应体按字段列出错误信息，例如 `{"episodeId": ["..."]}`、`{"cursor": ["Invalid cursor."]}`。登录失败时返回 `{"non_field_errors": [...]}`。冲突返回 409，响应体为 `{"detail": "...", "code": "..."}`。动作接口在契约中声明了必需的 `X-CSRFToken` 请求头。
- 列表使用游标：响应的 `nextCursor` 为 `null` 表示已到末页，否则把它作为下一页的 `cursor`，其余查询参数保持不变。游标绑定首页解析出的素材范围：`scope=current` 的后续页沿用首页所在的集，即使制作已进入下一集；改用其他范围请求时游标无效，返回 400。`limit` 默认为 50，最大为 200。每个查询响应读自同一个数据库快照。

## 项目

| 接口 | 用途 |
| --- | --- |
| `POST /projects` | 提交 `idea`，可附带客户端生成的 `projectId`。新建返回 201，用相同 UUID 和 Idea 重放返回 200 和原项目，UUID 已用于其他 Idea 时返回 409 `project_conflict`。新项目已暂停，根任务「短剧制作」等待中 |
| `GET /projects`、`GET /projects/{projectId}` | 列表按创建时间倒序。摘要含 `name`、`status`、`progress`、`currentEpisode`、`runningCount`、`timedOutCount`、`blockedCount` 和 `rootTaskId`。详情另含 `idea`、全剧规划的 `plan` 和集数 |
| `POST /projects/{projectId}/resume`、`/pause` | 幂等切换派发，返回最新摘要。首次 resume 写入 `startedAt`，即启动制作。已完成项目返回 409 `project_completed` |

`status` 为 `completed` 时根任务已完成；否则派发开启为 `running`，关闭为 `paused`。`startedAt` 为空的暂停项目尚未启动，界面可显示「启动制作」。全剧规划完成前，`name` 取 Idea 的首个非空行，规划后取剧名。`progress` 是 0 到 1 的结构进度，界面展示时再换算为百分比。可能出现 100% 而项目仍未完成的情况，此时以 `status` 为准。

`currentEpisode` 是正在制作的一集，全剧规划完成前和项目完成后为空。`GET /projects/{projectId}/episodes` 返回各集的状态、进度、镜数、是否已交付和是否当前集。`GET .../episodes/{episodeId}` 另含分集剧本，`GET .../episodes/{episodeId}/shots` 返回分镜顺序、场景、描述、预计时长、对白和音效提示，可用作镜头筛选项。剧本类文本保存在项目和分集记录中，不属于对象存储素材。

## 任务树与任务详情

`GET /projects/{projectId}/task-tree` 默认 `view=running`，返回所有运行任务和它们去重后的祖先，没有运行任务时只返回根节点。`view=subtree&rootTaskId=...` 返回该节点、直接子任务和孙任务，`path` 是该节点从根开始的祖先，可用于逐级返回。节点按创建时的位置顺序排列，每项含 `parentId`、`slotKey`、`state`、`progress`、`childTotal` 和已创建的 `childCount`。`name` 是中文任务名，例如「镜头 2 视频」，分集和镜号另见 `episodeNumber`、`shotNumber`。

- `GET /projects/{projectId}/tasks` 用于「定位运行」和「定位超时」，可按 `state`、`blocked` 和 `taskType` 筛选，每项的 `path` 是从根开始的祖先 ID。超时任务不在当前视图时，以 `path` 的末项为根打开子树。
- `GET /projects/{projectId}/tasks/{taskId}` 返回状态和祖先路径，以及 `dependencies` 和当前 `waits` 是否满足、`retryAt`、当前和最近 20 次执行。失败执行带 `errorCode` 和 `message`，超时执行的 `outcome` 为 `expired`。`outputCount` 是该任务实际产出的素材版本数，用于「查看 N 个产出」。
- `canRetry` 为真时显示「重试」，此时任务处于失败阻塞（`waiting` 且 `blockedReason` 非空）或 `timed_out`。`providerCheckPending` 为真时显示「待核查供应商任务」，`blockedReason` 为 `provider_jobs_unverified`。
- `POST /projects/{projectId}/tasks/{taskId}/retry` 清除阻塞并重置自动重试计数，返回更新后的详情。等待供应商核查时返回 409 `provider_check_pending`；正常等待、运行或完成时返回 409 `retry_unavailable`。重试后任务回到等待，项目处于运行时由 Worker 重新派发，新执行出现在 `executions` 首项。

## 素材库与交付

`GET /projects/{projectId}/assets` 按素材卡片分页，同一 `assetKey` 的版本归在一张卡片中。每张卡片带最新版本、版本数和已采用版本 `adoptedVersionId`。

| 参数 | 含义 |
| --- | --- |
| `scope=current` | 默认。有当前集时等同该集，否则为全剧共用。响应的 `scope` 和 `episode` 给出实际范围 |
| `scope=shared`、`all`、`episode` | 全剧共用、全项目，或配合 `episodeId` 的指定剧集 |
| `kind` | `visual` 为人物、场景和道具；`image` 为分镜图；`audio`、`video`；`delivery` 为分集交付的成片、字幕和时间轴；也可用具体的 `character`、`scene`、`prop`、`subtitles`、`timeline` |
| `shotId`、`q` | 镜头筛选，以及标题或素材键的不区分大小写搜索 |

结果按集、镜和素材键排序。`GET .../assets/{assetId}/versions` 返回版本历史，新版本在前。版本的 `adopted` 从生产记录推导：分镜采用的分镜图、视频、对白和音效，分集交付，以及关键素材任务选定的版本。候选版本不标为已采用。

`GET .../tasks/{taskId}/outputs` 返回该任务实际生成的全部确切版本及其所属素材，不受当前素材库范围和筛选影响。已完成的全剧规划、分集剧本和结构化任务在 `documents` 中指明文本产物所在的项目或分集。`GET .../deliveries` 按集分组返回每次交付的成片、字幕和时间轴版本，可用 `episodeId` 限定，`current` 表示分集当前采用的交付。

`GET .../asset-versions/{versionId}/preview` 为一个版本签发 5 分钟内有效的只读 GET 地址，并返回内容类型和大小。图片、音频、视频和 WebVTT 字幕以内联方式打开，其他类型（例如时间轴 JSON）以下载方式打开，避免在存储源上渲染可执行内容。预览接口的响应和对象读取响应都带 `no-store`，浏览器不会缓存私有素材。图片和视频可直接作为 `src` 使用，过期后重新请求。私有对象没有匿名读取，地址只授权该对象。签名地址使用 `STUDIO_S3_BROWSER_ENDPOINT`，Compose 开发环境为 `http://127.0.0.1:9000`，未设置时沿用 API 的 `STUDIO_S3_ENDPOINT`。存储未配置时返回 503。

## Django Admin

Admin 面向授权运维人员，用于只读查阅项目、分集、分镜、任务、执行、素材版本和交付记录。可执行的操作都调用领域服务：新建项目调用 `create_project`；启动或继续、暂停调用 `set_enabled`；重试调用调度器 `retry`，状态不允许时给出提示。Admin 不修改任务状态、产出或素材采用。

## 验证

后端测试 `apps/backend/tests/test_workbench_api.py` 使用真实 PostgreSQL，通过公开接口创建暂停项目、启动和暂停，读取运行路径、子树和进度，并覆盖合法与非法重试、供应商待核查、超时重试、项目隔离、素材版本、任务产出、交付和游标，以及 Admin 操作。`test_storage.py` 在 MinIO 上验证签名预览能读取已登记版本、未签名访问被拒绝。OpenAPI 与前端类型的同步按[仓库说明](../README.md#本地安装与验证)检查。
