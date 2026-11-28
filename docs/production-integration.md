# T02 生产后端接入

## 工作流接入（T03、T08）

HTTP 调用和本地 n8n 样例见[工作流接入](workflow-integration.md)。

使用 `configured_scheduler()` 加载注册表。Worker 通过 `WorkflowRequest` 提供 `name`、`schedule_id`、`task_id`、`task_type`、`definition_version`、`execution_id` 和 `deadline_at`。HTTP 层将 `schedule_id`、`definition_version` 分别映射为 `projectId`、`schemaVersion`。首次调用 `accept_workflow(execution_id)` 返回 `WorkflowAcceptance(status="accepted", payload=...)`，`payload` 是固定的业务输入。

`contracts.production_v1.LEAVES` 按任务类型提供工作流名、输入和成功结果的结构化数据校验器。`validate_leaf_input(task_type, value)` 校验输入，`validate_leaf_success(task_type, inputs, value)` 校验 `{"data": {...}, "outputs": [...]}` 及业务约束。样例覆盖所有叶子类型。[生产 v1 样例](../contracts/production-v1.examples.json)

| 工作流名 | 任务类型 |
| --- | --- |
| `series_plan` | `series_plan` |
| `episode_script` | `episode_script` |
| `episode_breakdown` | `episode_breakdown` |
| `visual_asset` | `character_asset`、`scene_asset`、`prop_asset` |
| `shot_image` | `shot_image` |
| `shot_audio` | `shot_dialogue`、`shot_sound` |
| `shot_video` | `shot_video` |
| `episode_edit` | `episode_edit` |

`characters` 可随全剧规划返回并传入分集剧本。人物配置由 `name` 和 `profile` 组成。分集新增素材按 `episodeNumber` 标记范围，全剧素材省略该字段。分集结构化通过已有的 `assetKey` 引用可复用素材，`data.assets` 只声明项目中尚无的素材键。

无对白时，分镜对话任务在本地直接完成。有对白时，配音按顺序提供逐句时长与时间戳。视频目标时长取分镜预计值与最后一句对白结束时间加 250 毫秒中的较大值，上限为 v1 支持的 24 小时。视频 `lipSync` 只含口型驱动配音，合成 `shots[].dialogue` 包含全部配音版本和时间戳。音效即使没有提示也会走 `shot_audio`，返回空的 `data.takes` 和 `outputs`。

回调使用 `scheduler.submit_workflow_result(execution_id, Completed(output), record_outputs=...)` 或 `Failed(...)`。`provider_busy` 表示供应商或服务暂时繁忙。生产叶子最多尝试三次，失败后分别退避 5 秒和 10 秒。需要核查供应商活动时，沿用 `confirm_provider_jobs_inactive`，由调度器在确认后安排重试。其他工作流错误码按原规则阻塞。

当前执行的成功包先由生产叶子校验，再进入同一个事务中的产物登记、生产数据写入、任务完成和进度更新。重复结果的状态为 `already_processed`。格式或业务校验失败后可按既有调度接口检查任务的 `blocked_reason`。

## 素材与交付登记（T04）

按 `record_outputs(execution, output)` 接入。登记函数收到通过叶子校验的成功包，并返回以输出槽位为键的引用，例如 `{"main": {"assetKey": "production:e1:s1:image", "kind": "image", "versionId": "<uuid>"}}`。每个声明槽位都应有返回值。返回的素材键和类型必须与对应描述一致。空 `outputs` 返回空字典。上传归属及对象校验在进入调度事务前完成，登记函数只写数据库。登记或生产写入抛出异常时，完成事务回滚，排除登记故障后可用同一执行重试回调。

工作流 HTTP 回调将 `media.workflow_outputs.prepare_workflow_outputs` 传给调度器。该函数校验上传归属和私有对象，然后返回写库用的登记函数。调度器先排除重复或过期执行并运行叶子结果校验，随后调用准备函数，最后在完成事务内再检查执行状态并调用登记函数。HTTP 入参、对象写入和版本读取见[素材与交付接入](media-integration.md)。

登记函数可从 `execution.task.schedule_id` 获取项目，从 `execution.snapshot["input"]` 取 `episodeNumber` 和 `shotNumber`。持久化行通过项目内集号、分集内镜号查找。工作台使用 `Episode.pk` 和 `Shot.pk` 作为 `episodeId` 和 `shotId`，在项目范围内解析。视频和合成交付中的 `kind` 都可以是 `video`，用 `task_type` 和输出槽位区分。

派生输出的 `assetKey` 由 v1 的 `production_asset_key` 生成。同一个镜头的候选图或视频共享素材键，每个输出槽位登记自己的版本。对白和音效的末尾编号按各自的输入顺序从 1 开始。全剧和分集的关键素材使用规划或结构化结果声明的 `assetKey`，`production:` 前缀保留给派生输出。

| 产物 | `assetKey` 示例 |
| --- | --- |
| 第 1 集第 2 镜的图和视频 | `production:e1:s2:image`、`production:e1:s2:video` |
| 第 1 集第 2 镜的首句对白和首个音效 | `production:e1:s2:dialogue:1`、`production:e1:s2:sound:1` |
| 第 1 集的成片、字幕和时间轴 | `production:e1:video`、`production:e1:subtitles`、`production:e1:timeline` |

生产任务按 `selectedSlot` 采用素材、分镜图或视频候选。选定素材续传到后续分集和分镜图，采用的分镜图用于首尾帧，逐句配音引用用于视频和合成，采用的视频及音效引用用于合成。引用都包含确切的 `versionId`。合成交付固定为 `video`、`subtitles`、`timeline`。全剧规划、剧本和结构化镜头使用已完成任务的结构化输出快照，作为后续任务的固定输入。

## 项目与工作台（T05）

`production.services.create_project(idea, *, project_id=None, definition_version="1")` 返回 `(project, root_task)`。项目主键是调度 UUID。创建时根任务类型为 `short_drama`，调度暂停，根任务等待。同一 UUID、规范化 Idea 和定义版本可安全重放。开始和暂停均通过 `set_enabled(project.pk, enabled)`。

生产域写入与对应工作流成功提交同步发生。

| 成功任务 | 生产数据 |
| --- | --- |
| `series_plan` | 写入 `Project.title`、`Project.plan`，按集序创建 `project.episodes` |
| `episode_script` | 写入 `Episode.script` |
| `episode_breakdown` | 按镜序创建 `episode.shots`，分镜说明与场景稳定键保存在 `Shot.spec` |
| `shot_image`、`shot_dialogue`、`shot_video`、`shot_sound` | 分别写入 `Shot.image`、`Shot.dialogue`、`Shot.video`、`Shot.sound` |
| `episode_edit` | 将三类已采用引用写入 `Episode.deliveries` |

项目规划返回前，界面可用 Idea 的首行显示临时名称。任务状态和进度从根任务读取，任务树及执行历史按 `schedule_id` 查询。参见 [T01 调度接入说明](scheduler-integration.md)。公开接口见 [T05 工作台 API 接入](workbench-api.md)。
