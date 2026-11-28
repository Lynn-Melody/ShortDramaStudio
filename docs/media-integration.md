# T04 素材与交付接入

接口见[内部 OpenAPI v1](../contracts/internal.yaml)，成片、字幕和时间轴的授权与回调见[素材 v1 调用样例](../contracts/media-v1.examples.json)。工作流身份、领取和状态检查沿用[工作流接入](workflow-integration.md)。

## 上传私有对象

1. 用工作流凭据向 `POST /internal/v1/executions/{executionId}/uploads` 提交 `projectId`、`taskId`、`executionId`、`schemaVersion`、`slot`、`sizeBytes` 和 `contentType`。执行须是当前、已领取且未到期的媒体任务。大小为 1 字节到 5 GiB 的整数，内容类型为单个 MIME 类型，例如 `video/mp4`。
2. 以返回的 `method`、`url` 和 `headers` 将原始文件字节直接写入私有对象存储。同一执行中的同一槽位重复申请相同大小和类型，会返回同一 `uploadId` 和对象键。签名地址每次可重签，最长有效 5 分钟且受执行截止时间限制。尺寸或类型变更返回 409，候选产物可使用不同槽位。
3. 签名绑定 `Content-Type`、`Content-Length` 和 `If-None-Match: *`。存储的首次成功写入保留该槽位的字节，重传时复用相同文件，MinIO 对已有对象返回 412。工作流可继续提交媒体回调，由平台核查对象是否符合已授权的元数据。 [S3 条件写入](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
4. 成功回调的每个 `outputs[]` 声明 `slot`、`assetKey`、`kind`、`title` 和对应的 `uploadId`。同一执行不同槽位、其他执行或其他项目的上传都会返回 400。对象缺失或元数据不符时返回 409，存储暂不可用时返回 503。工作流可以在对象写入成功或存储恢复后用相同身份重试。

Compose 中 API 已获得 `STUDIO_S3_ENDPOINT`、`STUDIO_MEDIA_BUCKET` 和开发存储机密。签名地址使用 API 配置的 S3 端点，工作流容器须能访问该地址。平台在事务外先检查所有上传的执行与槽位归属，再读取 HEAD 结果校验大小和类型。

## 登记与查询

平台在完成事务内登记该次执行的所有素材版本，并由生产后端记录所采用的版本。失败时回滚素材版本、交付记录、生产更新和任务完成，上传的私有对象保留供原执行重新提交。重复成功回调返回 `already_processed`，仍可追溯到原版本。

- `AssetItem` 在项目内按 `asset_key` 归并，保存类型与集、镜范围。候选槽位产生各自的 `AssetVersion`。版本的 `upload` 提供存储桶、对象键、大小、类型、生成执行与槽位。
- 工作台查询素材时按 `AssetItem.project_id` 限定项目范围。通过 `AssetVersion.upload__execution__task_id` 查某个任务的全部实际产物，通过 `versionId` 定位具体版本。采用标识从生产记录与固定输入推导。
- `episode_edit` 交付固定槽位为 `video`、`subtitles`、`timeline`。T09 使用这三个槽位上传并回调。每个 `EpisodeDelivery` 关联生成执行、所属分集和三份不可变素材版本。分集的 `deliveries` 保存已采用的三份 `{assetKey, kind, versionId}` 引用。

在运行 Compose 的环境中执行 `docker compose exec -T api pytest apps/backend/tests/test_storage.py`，使用真实 PostgreSQL 和 MinIO 验证签名上传、匿名访问拒绝、同槽不可覆盖、对象缺失或不符、跨执行和跨项目拦截，以及素材与交付的事务回滚。
