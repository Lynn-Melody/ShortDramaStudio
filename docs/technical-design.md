# AI 短剧工作室管理平台技术方案

## 技术决策

首版采用 **Django 5.2 LTS、Django REST Framework、PostgreSQL 任务调度和 React 工作台**。Django API 和独立 Worker 共用通用任务服务。具体任务类型采用继承 BaseTask 的类实现，在执行中决定子任务和依赖。n8n 执行单个生产任务，专业 Agent 在自建服务中运行。媒体文件存入私有对象存储。生产顺序遵循 [生产流程](production-flow.md)，工作台交互遵循 [产品设计](product-design.md)。

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
| `apps/backend/scheduler` | 通用任务、子任务位置、依赖和执行记录，BaseTask、任务类注册表、原子领取、结果提交、恢复与等权进度 |
| `apps/backend/production` | 项目、分集和分镜数据及具体生产任务类。负责生产流程、子任务定义、素材复用和媒体输入规则，调用通用任务服务 |
| `apps/backend/scheduler/management/commands` | 平台调度 Worker 入口 `manage.py run_scheduler`，扫描就绪和过期任务、派发本地任务类与外部工作流、恢复未送达记录 |
| `apps/backend/media` | 素材、版本和交付模型及迁移，素材库查询、产物登记和对象存储签名 |
| `apps/backend/contracts` | 版本化 DRF Serializer，定义 HTTP 请求响应和叶子任务输入输出 |
| `apps/agents` | 独立 Python 服务，通过内部 HTTP 接口运行九个专业角色，负责版本化配置、上下文、工具权限、运行记录和产物交接 |
| `workflows/n8n` | 可导入的生产工作流、任务类型到工作流的映射和联调样例 |

平台按 `task_id` 和 `execution_id` 跟踪工作。n8n 接受执行请求、调用 Agent 和媒体工具、回传产物。Agent 服务从带版本的设定、剧本和素材装配角色上下文。任务状态、重试、资产采用版本由平台事务更新。产品原型留在 `prototype/` 作为交互验收参照。

## 核心调度

通用调度的 Python 接入点见 [T01 调度模块接入说明](scheduler-integration.md)。生产任务、工作流和素材版本的接入约定见 [T02 生产后端接入](production-integration.md)。

### 任务类与持久化记录

任务类是任务处理器的面向对象实现。数据库任务记录保存状态，Python 对象在每次执行时由记录恢复。通过 `(task_type, definition_version)` 注册并定位具体类，所有类型继承 `BaseTask`。调度核心依赖基类协议，生产模块注册具体类。生产阶段、首尾帧、对白顺序和素材复用规则只在生产任务类中实现。

| 基类方法 | 职责 |
| --- | --- |
| `on_init()` | 创建任务时生成初始检查点、固定 `child_total` 和对应位置。仅初始化，不进行外部调用；重复创建请求不得重复初始化已持久化任务 |
| `on_processing()` | 读取不可变输入、检查点与子任务产出，决定本次创建、等待、完成或调用工作流。必须是无副作用的纯决策：不发起外部调用、不直接写库，只返回意图，以便重复投递时安全重跑 |
| `on_workflow_result(result)` | 工作流回调后由平台调用，校验并映射工作流产出，只能返回 `finish(output)` 或 `fail(error)`。同样无副作用 |
| `child(key, task_class, input, depends_on)` | 声明一个预留位置上的子任务，尚未直接写库 |
| `wait_for_children(children, wait_for, next_step)` | 返回待保存的子任务、依赖、等待位置集合及恢复检查点 |
| `finish(output)` | 返回完成结果，平台验证位置全部完成后原子提交 |
| `fail(error)` | 返回业务失败，携带稳定错误码和可展示说明 |
| `invoke_workflow(name, input)` | 返回外部工作流调用请求，由持久化派发器送达 |
| `get_parent_task()` | 读取父任务信息，子任务不直接修改父任务步骤 |

`cur_step` 等运行状态存入 `checkpoint`。构造 Python 对象时加载检查点，不重复调用 `on_init()`。初始化清单与参数在任务创建时固定，叶子任务 `child_total = 0`。初始化方案可在事务外准备，写入时校验输入版本和唯一键。项目绑定定义版本，已创建任务按绑定的类版本和检查点格式恢复。

以下为业务任务类的接口示意，非已实现代码。创建「制作分镜」时声明三个位置 `dialogue`、`video`、`sound`。

```python
class ShotTask(BaseTask):
    def on_init(self):
        self.child_total = 3
        self.child_keys = ["dialogue", "video", "sound"]
        self.cur_step = 0

    def on_processing(self):
        if self.cur_step == 0:
            return self.wait_for_children(
                children=[self.child("dialogue", ShotDialogueTask, self.input)],
                wait_for=["dialogue"],
                next_step=1,
            )
        if self.cur_step == 1:
            dialogue = self.child_output("dialogue")  # 实际时长、逐句时间戳与确切配音版本
            return self.wait_for_children(
                children=[
                    self.child("video", ShotVideoTask, {**self.input, "dialogue": dialogue}),
                    self.child("sound", ShotSoundTask, {**self.input, "dialogue": dialogue}),
                ],
                wait_for=["video", "sound"],
                next_step=2,
            )
        return self.finish({
            "video": self.child_output("video"),
            "sound": self.child_output("sound"),
        })
```

`child_output(key)` 读取已完成直属子任务的产出引用。`next_step` 是下一次恢复的位置。等待集合全部完成前，当前任务不能再次领取。下次执行收到已完成子任务的产出，具体类选择并固定需要的输入版本。无对白时，`ShotDialogueTask` 直接返回完成；有对白时调用音频工作流。

### 任务图和执行状态

- `parent_id` 只表示创建关系，用于任务树和进度汇总，不自动构成领取前置条件。`task_dependencies` 保存任务执行前置条件，`task_waits` 保存当前执行提交的直属子任务等待集合。校验环路时同时检查父任务最终完成所需的全部已创建子任务，端点限同一项目。
- 每个任务创建时确定直属子任务总数 `child_total`，并预留等量位置。位置不是可调度的任务，具体类在执行过程中填入真实子任务。`(parent_id, child_key)` 唯一，重复创建相同定义幂等，冲突定义拒绝提交。确定无需执行的位置由对应任务直接完成并记录原因。
- 任务只有 `waiting`、`running`、`timed_out`、`completed` 四种状态。执行记录单独使用 `queued`、`accepted`、`succeeded`、`failed`、`expired`。平台不单设"失败"状态：业务失败时任务回到 `waiting` 并设置 `blocked_reason`（下文"失败阻塞"即指此情形），显式重试后才可派发；超时进入 `timed_out`。两者保留已提交检查点和子任务。
- 各任务类型配置自动重试策略 `max_attempts` 与指数退避。超时、传输错误和工作流上报的可重试错误码在次数内自动重试：任务回到 `waiting`，到期时间写入 `retry_at`。用尽次数后，超时任务保持 `timed_out`，其他错误转为失败阻塞。业务失败（如审核超出返工次数）不自动重试，直接失败阻塞，等待显式重试。显式重试重置自动重试计数。
- 项目启用派发、本任务 `blocked_reason` 为空、已到 `retry_at`、前置依赖与当前等待集合全部完成时，等待任务可领取。单个任务的失败阻塞不影响项目内其他就绪任务。通过未满足依赖查询筛选就绪任务，无需每轮对全图进行拓扑排序。空批次由具体类直接完成。
- 创建项目时同事务创建等待中的根任务，项目初始暂停。用相同项目 UUID、根任务类型、定义版本和输入重放创建时返回已持久化的调度和根任务，相同 UUID 对应的其他定义或输入将被拒绝。暂停以领取事务为界，停止新领取，已经领取的执行可以送达和收尾。继续后再领取就绪任务。项目是否完成取决于根任务状态。

### 执行结果协议

| 结果 | 平台提交行为 |
| --- | --- |
| `completed` | 保存产出；校验所有预留子任务位置已完成；结束本次执行并完成任务；解除相关依赖阻塞，更新祖先进度 |
| `wait_for_children` | 一次事务保存新子任务及其初始化定义、依赖、非空等待集合和检查点；本次执行成功结束，当前任务转回等待 |
| `invoke_workflow` | 持久化工作流名、输入版本和 outbox 派发记录；当前任务及执行保持运行，等待工作流回调 |
| `failed` | 记录错误，结束本次执行，任务回到等待并阻塞，等待显式重试 |

`wait_for_children` 可引用新创建或已存在的直属子任务，前置依赖可引用同批子任务键或已有本项目任务 ID。通用服务验证位置归属、总数、同键定义、任务类型、引用和依赖环路，任一失败则整批回滚。完成子任务不会调用父对象的 `step_forward()`，父任务满足等待条件后重新执行自己的 `on_processing()`。

外部工作流调用使用当前执行 ID 和稳定调用键去重。调用意图持久化后，恢复派发使用 outbox，不再次执行该执行的业务决策。工作流只能以 `completed` 或 `failed` 回调，不能创建子任务或等待集合。平台在事务外调用任务类的 `on_workflow_result(result)`，由其校验并映射为 `completed` 或 `failed` 后再按上表提交。需要多轮外部操作的业务应拆成子任务。外部网络调用和任务类执行均在数据库锁事务之外进行。

### 领取、派发和回调

1. **领取。** Worker 按最早就绪顺序扫描启用派发的项目，在 `transaction.atomic()` 里用 `select_for_update(skip_locked=True, of=("self",))` 领取项目行，取得锁后重查状态、依赖和并发余额。同项目的创建、暂停、领取、结果提交、超时、重试均先锁项目行，再更新任务和执行。短事务中把就绪任务设为运行，固定输入与检查点快照，生成执行 ID，写入开始时间、截止时间及本地执行派发记录。 [Django 事务](https://docs.djangoproject.com/en/5.2/topics/db/transactions/) · [Django 行锁](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update)
2. **送达。** Worker 用可过期租约领取 outbox 记录，提交事务后通过注册表调用任务类的 `on_processing()`。本地执行同样按执行 ID 接收和提交，重复投递不能并发执行同一执行。任务类返回外部调用请求时，再通过持久化 outbox 调用对应 n8n Webhook。首个有业务逻辑的节点调用平台 `accept` 接口，原子接收该执行并登记已送达。重复请求返回 `already_accepted`，工作流据此结束。HTTP 超时或临时传输错误时，在截止时间内退避重发同一执行 ID。n8n 在接收检查后响应 Webhook，再继续生产。已接收的工作流可在 n8n 自己的执行记录中排查。 [n8n Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/) · [Respond to Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.respondtowebhook/)
3. **完成。** 回调先校验产物结构和上传归属，网络校验在事务外完成。锁定项目后，用 `clock_timestamp()` 检查当前执行 ID、执行状态和截止时间。只有当前、已接收且未到期的执行能修改任务。本地执行按执行结果协议，在同一事务中保存产物、子任务、依赖、等待集合和检查点；工作流回调经 `on_workflow_result()` 映射后只保存产物或失败原因。随后更新状态与进度。子任务完成只解除等待，后续子任务由父任务下次执行决定。重复回调返回已处理，现有产物引用和进度保持一致。
   **晚到收养。** 已超时执行晚到的成功结果，若该任务在其之后尚未分配新执行，则照常校验并提交，任务完成，避免丢弃已付费产出。其他迟到回调返回已过期。
4. **恢复。** Worker 定期回收超时执行和过期 outbox 租约，并按重试策略安排自动重试。执行失败或超时后，重试沿用任务 ID、已保存检查点和已有子任务，领取时分配新执行 ID。支持幂等键的外部服务使用该执行 ID 或其派生步骤键。工作流已接收后超时，或已登记供应商作业的执行失败（包括不可自动重试的失败），先核查该工作流的在途请求及供应商作业，再允许自动或手动重试。核查确认后，不可自动重试的失败仍保持阻塞，等待手动重试。供应商作业 ID 未登记时，从工作流执行记录及请求关联键核查，核查期间仍可登记迟到的作业 ID。网络送达按可重发设计，平台的产物登记与阶段推进按执行去重。

Worker 首版单进程运行，领取实现支持多实例并发。各任务类型配置执行期限和项目内并发上限，根据两镜头试制数据调整。日志以 `project_id`、`task_id`、`execution_id` 串联平台、n8n 与 Agent 记录。采集就绪等待时长、队列深度、派发重发、执行失败、超时和每类任务耗时。

### 业务流程与版本依赖

完整业务流程与任务结构见 [生产流程](production-flow.md)。修改生产步骤时修改具体任务类及对应版本，通用调度模块保持统一协议。项目在创建时绑定版本，恢复和重试使用原版本。

**首版不做**已完成任务的重跑、候选采用和更换口型驱动配音，不提供对应接口，与产品设计的后续版本范围一致。以下为预留设计：已完成任务重跑时创建独立候选任务，保留原任务、素材和固定输入版本。候选任务不占用原父任务的预留位置，不计入原生产进度；另行关联来源任务。需要交付重制时由具体任务类编排新执行链。失败重试则沿用原逻辑任务。

## 数据与接口

### 持久化模型

| 数据 | 最小职责与约束 |
| --- | --- |
| `projects`、`episodes`、`shots` | 项目 Idea 与生产数据。项目内集号唯一，分集内分镜顺序与业务 ID 分别唯一，场景用稳定 ID |
| `scheduler_schedule` | 通用调度范围、派发开关、开始时间、根任务定义版本和互斥行锁。生产项目可用相同 ID 关联 |
| `tasks` | 任务类型与定义版本、父节点、直属子任务总数、检查点、输入输出、状态、阻塞原因、自动重试计数与 `retry_at`、当前执行 ID。按项目、父节点、状态索引 |
| `task_child_slots` | 创建时固定的位置键，权重均为 1，可暂未绑定子任务；父任务与位置键唯一，每个位置至多绑定一个任务 |
| `task_dependencies`、`task_waits` | 执行前置依赖及当前恢复前等待集合，同项目无环；只有位置已绑定且对应任务完成才满足等待 |
| `task_executions`、`dispatch_outbox` | 每次尝试的请求快照、状态、截止时间和外部 job ID。每个任务至多一个正在执行的尝试，同次执行的本地决策和外部工作流各有一条派发记录 |
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
| `POST /projects/{projectId}/tasks/{taskId}/retry` | 失败阻塞（`waiting` 且 `blocked_reason` 非空）或 `timed_out` 的任务可重试，清除阻塞并重置自动重试计数。其他状态返回冲突 |
| `GET /projects/{projectId}/tasks/{taskId}/outputs` | 返回该任务生成的实际素材版本，按任务范围读取 |
| `GET /projects/{projectId}/assets` | `scope` 可为 `current`、`shared`、`all` 或 `episode`。可联合 `episodeId`、`kind`、`shotId`、`q` 和游标。交付按集分组成片、字幕和时间轴 |
| `GET /projects/{projectId}/assets/{assetId}/versions`、`GET /projects/{projectId}/asset-versions/{versionId}/preview` | 读取版本历史，签发指定版本的临时预览地址 |

前端用 React Flow 自定义状态圆点与返回交互，视图位置按项目和下钻路径缓存。任务树轮询更新节点状态和可见路径，选中节点、下钻位置和用户设置的视角由本地界面状态持有。首版对前台可见项目每 2 秒刷新摘要和当前视图，后台标签页暂停轮询。Idea 弹窗草稿留在页面状态，提交成功后选中新项目。默认使用明亮主题，主题选择持久化到本地。 [React Flow 受控视口](https://reactflow.dev/api-reference/react-flow#viewport-props)

素材库默认显示当前制作集，尚无当前集时显示全剧共用素材。打开任务产出视图时，将当前范围、筛选和滚动位置压入界面状态，读取该任务产出的全部确切版本。返回时恢复浏览状态，任务树位置由中栏独立保留。

### 等权递归进度

每个子任务在父任务中的权重均为 `1`。创建时固定 `child_total`，后续填充位置不改变分母。未填充的位置贡献为 `0`。叶子任务以及子任务总数为零的空批次，在任务完成前进度为 `0`，完成后为 `1`。

```text
child_total = 0 时：progress = 1（completed），否则为 0
child_total > 0 时：progress = 所有已绑定子任务的 progress 之和 / child_total
项目进度 = 根任务 progress
```

递归计算使用未取整的值，最后展示时才转换为百分比。无需执行的任务直接完成，照常贡献 `1`。完成事件在同一事务中自下而上逐级重算祖先：每个父任务按上式由直属子任务的当前值重新求和，不做增量累加，避免浮点漂移和重复累计；可以从持久化任务树重算。该值表示任务结构完成比例，不表示时间或成本。全部子任务完成但父任务尚在收尾时，计算进度可达 100%，界面仍按父任务状态显示运行或等待，下游仅依据完成状态启动。

### n8n 与 Agent 接口

内部 API 前缀为 `/internal/v1`，生产环境在内网上使用服务身份认证。平台向工作流传 `projectId`、`taskId`、`executionId`、`taskType`、`schemaVersion` 和 `deadlineAt`。工作流通过 `POST /executions/{executionId}/accept` 获得不可变输入快照和带角色的素材版本引用。首次接收返回 `accepted`，已接收或已终止时返回对应结果。身份字段、凭据和响应见[工作流接入](workflow-integration.md)。

- 工作流经 `POST /executions/{executionId}/uploads` 申请专属对象键和短期上传地址。对象键按执行和槽位唯一分配，防止覆盖；所选存储支持时，再在签名中加入 `If-None-Match: *` 条件写入作为附加保护，不作为联调硬性要求。平台在提交前用 HEAD 校验对象存在及大小。
- 工作流在发起付费或耗时步骤前调用 `GET /executions/{executionId}/status`，若执行已过期、已终止或不再是任务的当前执行，则停止后续步骤，避免超时后继续产生费用。经 `POST /executions/{executionId}/result` 回传 `projectId`、`taskId`、`executionId`、`schemaVersion` 和 `outcome`。成功结果携带结构化 `data` 以及 `outputs[]`，每项包括 `slot`、`assetKey`、`kind`、`title` 和 `uploadId`。`slot` 在单次执行内唯一。失败结果携带稳定的 `errorCode` 和可展示说明。过期或重复结果返回幂等响应，结构错误返回校验错误。 [S3 条件写入](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
- 外部媒体服务接受任务后，工作流经 `PUT /executions/{executionId}/provider-jobs/{stepKey}` 登记供应商和 job ID，重复登记相同 job ID 幂等。独立核查身份通过 `GET /executions/{executionId}/provider-check` 读取持久化快照，在确认工作流、在途请求和供应商作业后，通过同路径的 `POST` 提交所核查的快照。
- Agent 服务提供内部 `POST /agent-runs` 与 `GET /agent-runs/{runId}`。请求给出 `role`、`executionId`、`stepKey` 和输入版本引用。平台为每个角色固定模型、提示词、工具和输出 schema 版本。同一执行内的相同步骤键返回同一个运行记录。工作流以结构化产物交接，上下游依赖已持久化的版本引用。
- 具体生产任务的数据契约随任务类版本注册。全剧规划输出有序剧集和关键素材清单。剧本结构化输出有序镜头、`sceneId`、预计时长、对白和音效提示。对白输出选定音轨、实际时长及逐句时间戳。分镜图输出选定的图片槽位。视频输入包含本镜首帧、可选尾帧、可选的对白版本和目标时长。合成输出固定的 `video`、`subtitles`、`timeline` 三个槽位。输出结构由对应任务类型和 `schemaVersion` 校验。

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

开发环境的启动、校验和契约生成命令见[仓库说明](../README.md)。本地 Compose 先初始化独立数据库与私有开发桶，再启动依赖它们的进程。初始服务提供健康检查，生产任务、Agent 角色和渲染接口按以下顺序接入。

首版使用 Docker Compose，包含 Web、Django API、独立调度 Worker、Agent 运行服务、自托管 n8n、基于 FFmpeg 的渲染服务和 PostgreSQL；开发环境另含 MinIO 作为 S3 兼容存储。渲染服务通过内部 HTTP 接收可执行时间轴，从对象存储读取素材并回写成片和字幕。API 与 Worker 使用同一后端镜像，发布时先执行数据库迁移，再启动两个进程。开发环境使用同版本数据库和可替换的媒体适配器，联调配置独立的 S3 兼容测试桶。生产使用私有桶和独立凭据，浏览器经同域 HTTPS 网关访问工作台。首版单工作室共用项目，生产入口接入部署环境的统一身份认证。Admin 面向授权运维人员，任务状态和采用版本通过领域服务更新。n8n 和平台各用单独的 PostgreSQL 数据库与账号。备份平台数据库、n8n 数据库和加密密钥，媒体保留对象备份并定期做恢复演练。 [Django 管理命令](https://docs.djangoproject.com/en/5.2/howto/custom-management-commands/) · [n8n PostgreSQL 配置](https://docs.n8n.io/deploy/host-n8n/configure-n8n/choose-n8ns-database.md)

| 交付顺序 | 负责模块 | 验收 |
| --- | --- | --- |
| 1 | 调度与契约 | 先用与短剧无关的两种任务类验证串行、并行、条件直接完成、检查点恢复、固定位置与等权递归进度。生产类再用两集、每集两镜验证完整业务流程 |
| 2 | 工作流接入与媒体 | 先接真实对白和视频，再补合成。两镜分别验证有对白生成与无对白任务直接完成，均在对话任务完成后创建视频和音效并行任务，验证双任务汇合及全部分镜汇合。同场景尾帧、独立配音和交付三产物可追溯到确切版本 |
| 3 | 工作台 | 并发运行路径去重、无运行时保留根节点、两层下钻返回复原视角、暂停收尾、素材交付与主题保留。用数十集、每集数十镜的固定数据验证按范围加载 |
| 4 | Agent 服务 | 先接编剧、剧本审核和导演，再接视觉、声音与后期角色。用固定剧本和镜头评测剧情一致性、角色一致性和音画时序。换提示词或模型时跑回归 |

调度集成测试使用真实 PostgreSQL 和 `pytest.mark.django_db(transaction=True)`，以两个独立数据库连接并发争用同一任务。故障用例覆盖 Worker 领取后重启、Webhook 响应丢失、执行重复接收、结果重复回调、超时与成功竞争、暂停与领取竞争、旧执行迟到和原版本继续可读。PR 检查包含 Django 检查、后端单元与数据库测试、OpenAPI 契约与前端类型同步检查、工作台相关测试。每个模块先用共享契约和模拟结果独立验收，再接生产工具。 [pytest-django 事务测试](https://pytest-django.readthedocs.io/en/latest/database.html#testing-transactions)

通用调度验收还需覆盖位置超额与重复绑定、未完成位置禁止父任务完成、等待集合与依赖联合成环、重复等待结果不重复创建子任务、父任务恢复前重启、100% 进度但尚未收尾不解锁下游、自动重试达到 `max_attempts` 后转为阻塞、超时执行晚到收养与新执行分配后的拒绝、`on_processing()` 重复投递重跑结果一致，以及空批次和旧版本恢复。文档示意不替代后端实现测试。
