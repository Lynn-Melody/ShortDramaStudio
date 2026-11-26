# AI 短剧工作室管理平台技术方案

## 技术决策

首版采用 **Django 5.2 LTS、Django REST Framework、PostgreSQL 任务调度和 React 工作台**。Django API 和独立 Worker 共用生产领域服务，n8n 执行单个生产任务，专业 Agent 在自建服务中运行。媒体文件存入私有对象存储。生产顺序遵循 [项目约定](../AGENTS.md)，工作台交互遵循 [产品设计](product-design.md)。

| 层级 | 选型 | 用途 |
| --- | --- | --- |
| 工具链 | 后端使用 Python 3.14、uv。前端使用 Node.js 24 LTS、TypeScript、pnpm | Python 和前端依赖通过各自的锁文件固定 |
| 前端 | React 19、Vite、TanStack Query、React Flow | 左中右工作台、数据缓存、受控任务树视口 |
| API 与契约 | Django 5.2 LTS、Django REST Framework、drf-spectacular | DRF Serializer 校验请求和结果，生成 OpenAPI 3.1。前端使用 `openapi-typescript` 生成类型 |
| 持久化 | PostgreSQL 18、psycopg 3、Django ORM 与迁移 | 任务事务、行锁、派发记录、素材元数据和版本关联 |
| 内部运维 | Django Admin | 查阅项目、任务、执行记录和素材版本。允许的重试操作调用生产领域服务 |
| 媒体存储 | S3 兼容对象存储、boto3 | 私有存储图片、音视频、字幕、剧本和时间轴，按需签发临时上传与读取地址 |
| 生产执行 | 独立 Django Worker、自托管 n8n、自建 Python Agent 服务 | 生产工作流接入九个专业角色。通过 HTTP 连接平台、工作流和 Agent |
| 测试与部署 | pytest、pytest-django、Vitest、Playwright、Docker Compose | 单元测试、真实 PostgreSQL 集成测试、工作台端到端测试与首版单机部署 |

项目可能累计数千节点。Django 提供项目和任务等关系数据的模型、迁移与内部运维入口。运行、暂停、回调和素材版本登记由 PostgreSQL 事务保证一致。Worker 通过行锁领取任务，媒体文件走对象存储，任务表持久化引用与可查询元数据。安装 Django 5.2 系列的受支持补丁版本，Python 3.14 兼容性从 5.2.8 起提供。 [Django 支持周期](https://www.djangoproject.com/download/#supported-versions) · [Python 3.14 兼容性](https://docs.djangoproject.com/en/5.2/releases/5.2.8/) · [Django Admin](https://docs.djangoproject.com/en/5.2/ref/contrib/admin/) · [PostgreSQL 锁定子句](https://www.postgresql.org/docs/18/sql-select.html#SQL-FOR-UPDATE-SHARE) · [S3 预签名 URL](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html)

## 模块边界与所有权

| 目录 | 职责和对外接口 |
| --- | --- |
| `apps/web` | 项目列表、任务树、任务详情、素材库和主题，通过公开 REST 接口读取或操作项目 |
| `apps/backend/studio` | Django 项目配置、身份校验、DRF 路由挂载、OpenAPI 生成和 Admin 入口 |
| `apps/backend/production` | 项目、分集和任务模型及迁移，生产阶段、状态事务、进度算法、控制接口和执行回调。Django API 与 Worker 调用同一套服务 |
| `apps/backend/production/management/commands` | 平台调度 Worker 入口 `manage.py run_scheduler`，扫描就绪和过期任务、派发 n8n Webhook、恢复未送达记录 |
| `apps/backend/media` | 素材、版本和交付模型及迁移，素材库查询、产物登记和对象存储签名 |
| `apps/backend/contracts` | 版本化 DRF Serializer，定义 HTTP 请求响应和叶子任务输入输出 |
| `apps/agents` | 独立 Python 服务，通过内部 HTTP 接口运行九个专业角色，负责版本化配置、上下文、工具权限、运行记录和产物交接 |
| `workflows/n8n` | 可导入的生产工作流、任务类型到工作流的映射和联调样例 |

平台按 `task_id` 和 `execution_id` 跟踪工作。n8n 接受执行请求、调用 Agent 和媒体工具、回传产物。Agent 服务从带版本的设定、剧本和素材装配角色上下文。任务状态、重试、资产采用版本由平台事务更新。产品原型留在 `prototype/` 作为交互验收参照。

## 核心调度

### 任务图和执行状态

- `parent_id` 表示任务的创建关系，用来构造工作台任务树。`task_dependencies` 表示执行前置条件，包含本镜视频依赖本镜对白等关系。依赖在创建任务时写入，端点限同一项目，构图时检查无环。
- 编排任务推进一个阶段，创建子任务后回到等待。顺序步骤只创建当前子任务。并行批次先一次性登记成员，成员全部完成后汇合，空批次直接完成。子任务用 `(parent_id, child_key)` 去重，根任务每项目唯一。
- 任务只有 `waiting`、`running`、`timed_out`、`completed` 四种状态。执行单独记录 `queued`、`accepted`、`succeeded`、`failed`、`expired` 以及截止时间和结果。待送达和已接收的执行都可能到期。业务失败结束当前执行，任务回到 `waiting` 并标记 `execution_failed`，由显式重试清除阻塞。真超时进入 `timed_out`。两种情况均保留已有产物和执行记录。
- 创建项目时同事务创建等待中的根任务，项目展示「已暂停」。首次「继续」记录启动时间，之后「暂停／继续」切换派发开关。已完成项目的状态由根任务推导。「暂停」以领取事务为界，之后停止新领取，已经领取的执行可以送达和收尾。回调可登记产物及创建等待中的后继任务。继续后再领取。

### 领取、派发和回调

1. **领取。** Worker 按最早就绪顺序扫描启用派发的项目，在 `transaction.atomic()` 里用 `select_for_update(skip_locked=True, of=("self",))` 领取项目行，取得锁后重查状态、依赖和并发余额。同项目的创建后推进、暂停、领取、回调、超时、重试均先锁项目行，再更新任务和执行。短事务中把叶子任务设为运行，固定本次输入版本，生成执行 ID，写入开始时间、截止时间及一条 `dispatch_outbox`。编排推进在同一套项目锁规则下完成。 [Django 事务](https://docs.djangoproject.com/en/5.2/topics/db/transactions/) · [Django 行锁](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update)
2. **送达。** Worker 用可过期租约领取 outbox 记录，提交事务后调用对应 n8n Webhook。首个有业务逻辑的节点调用平台 `accept` 接口，原子接收该执行并登记已送达。重复请求返回 `already_accepted`，工作流据此结束。HTTP 超时或临时传输错误时，在截止时间内退避重发同一执行 ID。n8n 在接收检查后响应 Webhook，再继续生产。已接收的工作流可在 n8n 自己的执行记录中排查。 [n8n Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/) · [Respond to Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.respondtowebhook/)
3. **完成。** 回调先校验产物结构和上传归属，网络校验在事务外完成。锁定项目后，用 `clock_timestamp()` 检查当前执行 ID、执行状态和截止时间。只有当前、已接收且未到期的执行能修改任务。成功时，同事务登记素材版本、标记完成、推进父任务、创建后继节点、更新项目进度。重复或迟到回调返回已处理或已过期，现有产物引用和进度保持一致。
4. **恢复。** Worker 定期回收超时执行和过期 outbox 租约。执行失败或超时后，重试沿用任务 ID，领取时分配新执行 ID。支持幂等键的外部服务使用该执行 ID 或其派生步骤键。已接收但结果不明的付费调用，先按外部 job ID 核查再重试。网络送达按可重发设计，平台的产物登记与阶段推进按执行去重。

Worker 首版单进程运行，领取实现支持多实例并发。各任务类型配置执行期限和项目内并发上限，根据两镜头试制数据调整。日志以 `project_id`、`task_id`、`execution_id` 串联平台、n8n 与 Agent 记录。采集就绪等待时长、队列深度、派发重发、执行失败、超时和每类任务耗时。

### 生产步骤与版本依赖

- 根任务依次推进全剧规划、全局关键素材生成、全部剧集制作。剧集按规划的集序一次派发一集，本集成片、字幕和时间轴齐备后推进下一集。
- 单集依次推进制作分集剧本、分集剧本结构化、分集关键素材制作、制作分集全部分镜图、制作所有分镜、剪辑与合成。分镜方案先定义素材需求。人物、场景和道具按稳定业务 ID 复用或并行补齐，**所需素材全齐才并行出分镜图**。
- 所有分镜图及采用版本齐备后，「制作所有分镜」获取本集分镜列表，并行创建「制作分镜」子任务。每个分镜先创建「制作分镜对话音频」子任务，根据 `dialogue` 调用「制作音频 n8n 工作流」；无输入对话时仍创建该任务，直接完成且不调用工作流。对话音频任务完成后，再并行创建「制作分镜视频」和「制作分镜音效」子任务；音效根据 `sound` 调用同一条「制作音频 n8n 工作流」。视频与音效全部完成后，本分镜完成；所有分镜完成后进入剪辑与合成。各分镜独立推进。视频读取实际配音时长，无对白时读取预计时长。音效在剪辑时按画面对齐。本镜图作首帧，同场景的下一镜图作尾帧，跨场景和末镜留空。口型驱动与最终合成引用同一版对白。
- 半成品失败重试沿用同一逻辑任务。已完成任务重跑时，创建独立的候选任务，保留历史版本和已固定的引用。候选任务不计入原生产进度。需要替换口型驱动对白时，由平台显式派发受影响镜头和合成的新执行链，沿用既有分镜与素材的版本引用。首版工作台自动按任务推进，人工审核和批量操作属于后续产品范围。

## 数据与接口

### 持久化模型

| 数据 | 最小职责与约束 |
| --- | --- |
| `projects`、`episodes`、`shots` | 项目 Idea、派发开关、任务定义版本。项目内集号唯一，分集内分镜顺序与业务 ID 分别唯一，场景用稳定 ID |
| `tasks`、`task_dependencies` | 任务类型、父节点、当前阶段、状态、阻塞原因、当前执行 ID。支持按项目、父节点和状态索引 |
| `task_executions`、`dispatch_outbox` | 每次尝试的请求快照、状态、截止时间、外部 job ID。每个任务至多一个正在执行的尝试，每个执行唯一派发记录 |
| `asset_items`、`asset_versions` | 每项目用业务键归并同一份素材，版本不可变。保存类型、集与镜头范围、对象键、生成执行及必要的媒体元数据 |
| `execution_uploads`、`execution_inputs`、`task_outputs` | 约束上传归属，记录每次执行采用的确切素材版本和实际产出。按执行及输出槽位做唯一约束 |
| `episode_deliveries` | 每集的交付版本，关联成片、字幕和时间轴三份确切产物 |

素材卡片按 `asset_item` 分组，生成执行引用用于追溯。采用标识从生产输入绑定推导，候选版本会产生独立引用。依赖任务领取时固定输入版本，重试复用该次生产任务已经固定的输入。调度定义和 Agent 配置版本随执行保存。数据库使用 UTC 时间，镜头时长与时间戳统一为整数毫秒。

### 工作台接口

公共 API 前缀为 `/api/v1`，列表使用稳定游标。应用以项目 ID 做查询范围校验。动作接口返回更新后的摘要，查询接口在同一次响应中返回一致的读取结果。

API 与内部接口分别从后端 Serializer 生成 OpenAPI 3.1。前端从公共契约生成 TypeScript 类型，工作流和 Agent 以内部契约及对应的 `schemaVersion` 联调。CI 检查契约与生成类型同步。 [drf-spectacular 设置](https://drf-spectacular.readthedocs.io/en/latest/settings.html) · [openapi-typescript](https://openapi-ts.dev/introduction)

| 方法与资源 | 契约 |
| --- | --- |
| `POST /projects`、`GET /projects`、`GET /projects/{projectId}` | 创建接收 `idea`，返回项目和根任务。摘要包含名称、状态、进度、当前集、运行数和超时数 |
| `POST /projects/{projectId}/pause`、`POST /projects/{projectId}/resume` | 幂等切换派发。首次 resume 完成启动 |
| `GET /projects/{projectId}/task-tree` | `view=running` 返回所有运行节点及去重后的祖先。`view=subtree&rootTaskId=...` 返回指定节点及两层后代 |
| `GET /projects/{projectId}/tasks`、`GET /projects/{projectId}/tasks/{taskId}` | 首条用于按状态定位，返回祖先路径。详情返回状态、依赖、等待或超时原因、当前和历史执行摘要及产出数 |
| `POST /projects/{projectId}/tasks/{taskId}/retry` | 失败或已超时的任务可重试。其他状态返回冲突 |
| `GET /projects/{projectId}/tasks/{taskId}/outputs` | 返回该任务生成的实际素材版本，按任务范围读取 |
| `GET /projects/{projectId}/assets` | `scope` 可为 `current`、`shared`、`all` 或 `episode`。可联合 `episodeId`、`kind`、`shotId`、`q` 和游标。交付按集分组成片、字幕和时间轴 |
| `GET /projects/{projectId}/assets/{assetId}/versions`、`GET /projects/{projectId}/asset-versions/{versionId}/preview` | 读取版本历史，签发指定版本的临时预览地址 |

前端用 React Flow 自定义状态圆点与返回交互，视图位置按项目和下钻路径缓存。任务树轮询更新节点状态和可见路径，选中节点、下钻位置和用户设置的视角由本地界面状态持有。首版对前台可见项目每 2 秒刷新摘要和当前视图，后台标签页暂停轮询。Idea 弹窗草稿留在页面状态，提交成功后选中新项目。默认使用明亮主题，主题选择持久化到本地。 [React Flow 受控视口](https://reactflow.dev/api-reference/react-flow#viewport-props)

素材库默认显示当前制作集，尚无当前集时显示全剧共用素材。打开任务产出视图时，将当前范围、筛选和滚动位置压入界面状态，读取该任务产出的全部确切版本。返回时恢复浏览状态，任务树位置由中栏独立保留。

**进度是待产品确认的暂定默认。** 全剧规划、全局素材、全部剧集先用 `10% / 15% / 75%`。规划就绪后固定剧集清单，剧集份额均分。单集六阶段权重暂用 `10 / 10 / 10 / 20 / 35 / 15`，并行阶段按一次登记的批次成员计算。阶段未展开时贡献为零，空批次完成时计满，展示向下取整，根任务完成前最高 `99%`，完成后展示 `100%`。权重和任务定义版本随项目固定，进度不会因新增任务而倒退。

### n8n 与 Agent 接口

内部 API 前缀为 `/internal/v1`，生产环境内网加服务身份认证。平台向工作流传 `projectId`、`taskId`、`executionId`、`taskType`、`schemaVersion` 和 `deadlineAt`。工作流通过 `POST /executions/{executionId}/accept` 获得不可变输入快照和带角色的素材版本引用。首次接收返回 `accepted`，已接收或已终止时返回对应结果。

- 工作流经 `POST /executions/{executionId}/uploads` 申请专属对象键和短期上传地址，使用签名覆盖的 `If-None-Match: *` 条件上传。联调必须验证所选存储的条件写入和读取。经 `POST /executions/{executionId}/result` 回传 `taskId`、`executionId`、`schemaVersion`、`outcome`。成功结果携带结构化 `data` 以及 `outputs[]`，每项最少包括 `slot`、`assetKey`、`kind`、`title` 和 `uploadId`。`slot` 在单次执行内唯一。失败结果携带稳定的 `errorCode` 和可展示说明。过期或重复结果返回幂等响应，结构错误返回校验错误。 [S3 条件写入](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
- 外部媒体服务接受任务后，工作流经 `PUT /executions/{executionId}/provider-jobs/{stepKey}` 登记供应商和 job ID，重复登记相同 job ID 幂等。平台查询历史执行时一并显示，供超时后核查。
- Agent 服务提供内部 `POST /agent-runs` 与 `GET /agent-runs/{runId}`。请求给出 `role`、`executionId`、`stepKey` 和输入版本引用。平台为每个角色固定模型、提示词、工具和输出 schema 版本。同一执行内的相同步骤键返回同一个运行记录。工作流以结构化产物交接，上下游依赖已持久化的版本引用。
- 关键叶子契约优先冻结。全剧规划输出有序剧集和关键素材清单。剧本结构化输出有序镜头、`sceneId`、预计时长、对白和音效提示。对白输出选定音轨、实际时长及逐句时间戳。分镜图输出选定的图片槽位。视频输入包含本镜首帧、可选尾帧、可选的对白版本和目标时长。合成输出固定的 `video`、`subtitles`、`timeline` 三个槽位。输出结构由对应任务类型和 `schemaVersion` 校验。

| 工作流 | 叶子任务类型 | 专业 Agent | 主产出 |
| --- | --- | --- | --- |
| 全剧规划 | `series_plan` | 编剧、剧本审核、美术总监 | 故事设定、分集规划、全局素材清单、视觉规范 |
| 制作分集剧本 | `episode_script` | 编剧、剧本审核 | 已通过自动审核的分集剧本 |
| 分集剧本结构化 | `episode_breakdown` | 导演 | 有序分镜、对白和新增素材清单 |
| 生成关键视觉素材 | `character_asset`、`scene_asset`、`prop_asset` | 图片制作 | 人物、场景或道具参考图，供全剧和分集共用 |
| 制作分镜图 | `shot_image` | 分镜导演、图片制作 | 分镜图候选及选定版本 |
| 制作音频（制作分镜对话音频） | `shot_dialogue` | 声音导演 | 独立配音、实际时长与时间戳 |
| 制作分镜视频 | `shot_video` | 分镜导演、视频制作 | 按分镜图和实际对白节奏生成的视频 |
| 制作音频（制作分镜音效） | `shot_sound` | 声音导演 | 环境音和动作音效 |
| 剪辑与合成 | `episode_edit` | 后期剪辑 | 时间轴、字幕和渲染成片 |

对话音频与音效是两个任务类型，共用「制作音频 n8n 工作流」，通过任务类型区分输入和处理分支。原九条工作流清单中的两条音频工作流合并后，当前共八条独立工作流。

美术总监在全剧规划时发布视觉规范。导演负责整集分镜，分镜导演细化单镜的构图和表演。编剧和剧本审核在同一工作流内自动交接，返工次数由工作流配置限定，超过后回传业务失败。媒体生成和渲染由工作流调用工具，专业 Agent 提供审核结果、参数或可执行时间轴。

## 部署与协作验收

首版使用 Docker Compose，包含 Web、Django API、独立调度 Worker、Agent 运行服务、自托管 n8n 和 PostgreSQL。API 与 Worker 使用同一后端镜像，发布时先执行数据库迁移，再启动两个进程。开发环境使用同版本数据库和可替换的媒体适配器，联调配置独立的 S3 兼容测试桶。生产使用私有桶和独立凭据，浏览器经同域 HTTPS 网关访问工作台。首版单工作室共用项目，生产入口接入部署环境的统一身份认证。Admin 面向授权运维人员，任务状态和采用版本通过领域服务更新。n8n 和平台各用单独的 PostgreSQL 数据库与账号。备份平台数据库、n8n 数据库和加密密钥，媒体保留对象备份并定期做恢复演练。 [Django 管理命令](https://docs.djangoproject.com/en/5.2/howto/custom-management-commands/) · [n8n PostgreSQL 配置](https://docs.n8n.io/deploy/host-n8n/configure-n8n/choose-n8ns-database.md)

| 交付顺序 | 负责模块 | 验收 |
| --- | --- | --- |
| 1 | 调度与契约 | 创建项目后根任务等待。模拟工作流跑通两集、每集两镜，覆盖素材屏障、单镜对白依赖、末镜无尾帧、空批次和逐集交付 |
| 2 | 工作流接入与媒体 | 先接真实对白和视频，再补合成。两镜分别验证有对白生成与无对白任务直接完成，均在对话任务完成后创建视频和音效并行任务，验证双任务汇合及全部分镜汇合。同场景尾帧、独立配音和交付三产物可追溯到确切版本 |
| 3 | 工作台 | 并发运行路径去重、无运行时保留根节点、两层下钻返回复原视角、暂停收尾、素材交付与主题保留。用数十集、每集数十镜的固定数据验证按范围加载 |
| 4 | Agent 服务 | 先接编剧、剧本审核和导演，再接视觉、声音与后期角色。用固定剧本和镜头评测剧情一致性、角色一致性和音画时序。换提示词或模型时跑回归 |

调度集成测试使用真实 PostgreSQL 和 `pytest.mark.django_db(transaction=True)`，以两个独立数据库连接并发争用同一任务。故障用例覆盖 Worker 领取后重启、Webhook 响应丢失、执行重复接收、结果重复回调、超时与成功竞争、暂停与领取竞争、旧执行迟到和原版本继续可读。PR 检查包含 Django 检查、后端单元与数据库测试、OpenAPI 契约与前端类型同步检查、工作台相关测试。每个模块先用共享契约和模拟结果独立验收，再接生产工具。 [pytest-django 事务测试](https://pytest-django.readthedocs.io/en/latest/database.html#testing-transactions)
