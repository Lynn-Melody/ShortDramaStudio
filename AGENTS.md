# 开发协作指南

## 开始前

根据派发的任务选择下方对应角色，按本次授权的交付范围执行。开发任务需要目标、范围、验收标准和工作分支。审核任务需要任务目标、源分支、基线提交、待审提交号和 PR 链接。合并巡检以 GitHub PR 队列为入口。缺少会影响执行的信息时，向任务发起方确认。

先用 `git rev-parse --show-toplevel` 和 `git status --short --branch` 确认当前 worktree、分支和已有改动，保留其他人的改动。遇到子目录的 `AGENTS.md` 时，同时遵循其中适用于当前工作的约定。

按任务查阅相关文档：

- [生产流程](docs/production-flow.md)：当前生产顺序、分镜制作规则及业务任务结构。
- [技术方案](docs/technical-design.md)：通用调度核心、面向对象任务类、持久化、进度与协作接口。
- [产品设计](docs/product-design.md)：管理平台功能与交互。
- [本地安装与验证](README.md#本地安装与验证)：环境启动、本地验证和契约生成命令。
- [GitHub Actions 工作流](.github/workflows/development.yml)：PR 的 CI 检查。

修改相关规则时同步对应文档与校验。生产流程由具体任务类实现，调度核心只负责通用生命周期和任务关系。

## 风险级别

开发 Agent 在 PR 中标明风险级别，审核者和合并负责人可以上调，不可下调。

- **高风险**：改动调度核心或任务生命周期、数据库迁移、共享契约（`contracts/`、OpenAPI、生成类型）、`compose.yaml`、`infra/`、`.github/`，或同时改动两个以上模块。需要一个 GPT 会话和一个 Claude 会话分别审核。
- **标准**：其余改动。需要一位审核者，使用与作者不同的模型，例如 Codex 编写的任务由 Claude 审核。

## 开发 Agent

1. **开发和提交。** 在任务分配的独立 worktree 和任务分支上工作。复杂任务拆成可验证的步骤，每步编写或更新相关测试、通过对应验证，再创建一个 Git commit。只暂存本任务的文件，用 `git diff --cached` 核对提交范围。共享接口的字段或行为改变时，更新受影响的 OpenAPI、生成类型和调用示例，由实际使用该接口的 Agent 核对兼容性。例如修改工作台 API 的响应时，更新公开契约和前端类型，请工作台 Agent 核对页面调用。在已启用格式化钩子的 worktree 中，若提交因格式化暂停，检查差异并重新暂存对应文件后再提交。
2. **验证并开草稿 PR。** 按验收标准和 [README](README.md#本地安装与验证) 跑相关测试，记录实际运行的命令和结果。将任务分支推送到 `origin`，立即向 `main` 开草稿 PR，写明任务编号、变更范围、依赖和风险级别。分支推送本身不触发 CI，PR CI 是唯一的 CI 门禁。用 `git rev-parse HEAD` 取得最终提交号。PR CI 失败由作者在本地修复并推送新提交。
3. **送审。** 推送后即可送审，无需等待 PR CI；审核结论只在同一提交的 PR CI 通过后生效。按风险级别新开全新上下文的独立只读审核会话，可由作者用 `claude -p` 或 `codex exec` 无头启动，也可交协调方启动。给审核者任务目标、验收标准、源分支、基线、最终提交号和 PR 链接。遇到阻塞意见，作者在本地修复、补回归测试并推送，再由提出该意见的审核者复核修复增量。每份结论都应标明实际通过的提交号。
4. **转为正式 PR。** 审核通过且该提交的 PR CI 通过后，在 PR 中补充已审核的提交号、各审核结论、本地测试以及 PR CI 链接，再将 PR 标记为可审阅，即完成交接。需要尽快合入时，直接通知集成与合并负责人。后续新提交按下方审核有效性规则处理，并更新 PR 中记录的提交号。

### 审核有效性

- 新提交是合并 `main` 的合并提交（例如 GitHub 的 Update branch），父提交包含已审提交，且 `git show --cc "<新提交号>"` 没有冲突解决差异时，原审核继续有效，只需新提交的 PR CI 通过。
- 其他新提交由原审核者复核 `git diff "<已审提交号>..<新提交号>"`，无需从头重审。改动使风险级别上调时，补齐对应的审核。

### 查询 PR CI

有 GitHub CLI 时，可从仓库根目录查询。确认 `Development checks` 运行的 `headSha` 与送审提交一致、`conclusion` 为 `success`：

```powershell
gh pr checks "<PR编号>" --watch
gh run list --branch "<任务分支>" --commit "<最终提交号>" --event pull_request --json databaseId,workflowName,headSha,status,conclusion,url
gh run view "<CI 运行 ID>" --json headSha,status,conclusion,url
```

也可在 GitHub PR 页面查看同一提交的检查。CI 尚未登记、被跳过或因权限无法查看时，记录实际状态，不将 PR 标记为可审阅。只交付本地改动的任务，报告分支、提交号、本地验证结果及停在哪一步。

PR 并行运行两个作业，都在 GitHub 生成的合并提交上执行。`quick` 验证补丁空白、文档、原型、Django/PostgreSQL 和前端。`verify` 核对合并父提交，启动 Compose 并验证对象存储与浏览器链路：默认只启动 PostgreSQL、MinIO、API 和工作台；改动 `compose.yaml`、`infra/`、Agent、渲染、n8n 工作流、Python 依赖或 CI 工作流时启动完整 Compose。镜像层、pnpm 依赖和 MinIO 镜像都有缓存，`main` 的推送会预热缓存。

## 审核 Agent

1. 核对派发的基线、待审提交号和 PR 链接。需要时用 `git fetch origin "<任务分支>"` 取得待审提交。初审用 `git diff "<基线提交号>...<待审提交号>"` 查看完整任务差异，对照验收标准和相关设计核查行为、失败路径、接口兼容及测试覆盖。复核时用 `git diff "<上次送审提交号>..<当前提交号>"` 只看增量，确认阻塞问题已解决且修复没有引入回归。
2. 保持只读，各自给出独立结论。作者负责后续代码修改。审核意见写明文件和行号、触发条件、实际影响，并区分阻塞问题与普通建议。
3. 在审核会话交付 `通过` 或 `阻塞`，附实际审核的提交号、问题清单及验证范围。区分自己运行的测试与引用的 CI 结果，注明审核时该提交 PR CI 的状态。如果无法取得指定提交，报告缺项，等材料齐备后再审核。

## 集成与合并负责人

北京时间每天 09:00–23:00，每 30 分钟巡检一次，09:00 首轮，22:30 末轮。作者通知时可随时处理。查看指向 `main` 的非草稿 PR，按依赖顺序处理。用 `gh pr list --base main --state open` 或仓库的 Pull Requests 页面找候选 PR。没有符合条件的 PR 时，结束本轮。

- 核对 PR 最新提交号与风险级别要求的审核证据一致（或符合审核有效性规则）、阻塞意见已解决、PR CI 通过，以及前置 PR 已合并。用 `gh pr view <PR编号> --json headRefOid` 和 `gh pr checks <PR编号>` 查询，或直接查看 GitHub 页面。查看 CI 的 `Record tested PR merge parents` 日志，两个父提交应依次是当前 `main` 和 PR 最新提交。若 `main` 已前进，可直接用 GitHub 的 Update branch 更新分支，等新提交的 PR CI 通过；有冲突时交回作者。缺少任何一项就留待后续巡检。
- 通过 GitHub 合并，锁定核验过的提交号。使用 CLI 时附上 `--match-head-commit <已核验提交号>`。仓库启用 Merge Queue 时，核验后加入队列即可，由队列在最新 `main` 上重跑检查。如果合并前提交发生变更，重新核对新提交的 CI 和审核记录。
- 记录合入结果。跨模块改动在集成结果上用两集四镜样例验收，覆盖项目创建到交付、暂停继续、Worker 重启和可恢复失败。
