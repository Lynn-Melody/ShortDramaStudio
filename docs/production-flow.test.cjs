'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync, existsSync } = require('node:fs');
const { resolve, dirname } = require('node:path');

// Documentation checks only; runtime scheduling tests belong to the future backend.
for (const file of ['docs/production-flow.md', 'docs/product-design.md']) {
  test(`${file} keeps the agreed shot workflow consistent`, () => {
    const text = readFileSync(resolve(__dirname, '..', file), 'utf8');
    assert.ok(text.includes('制作所有分镜'));
    assert.ok(!text.includes('生成所有分镜视频及音频'));
    assert.match(text, /无输入对话[\s\S]*?直接完成/);
    assert.match(text, /对话音频任务完成后，再并行创建「制作分镜视频」[和与]「制作分镜音效」/);
    assert.match(text, /视频[和与]音效[\s\S]*?全部完成后/);
    assert.match(text, /所有分镜完成后[，进]/);
    assert.ok(text.includes('`dialogue`'));
    assert.ok(text.includes('`sound`'));
    assert.match(text, /同一条「制作音频 n8n 工作流」/);
  });
}

test('collaboration guide delegates production rules to the dedicated document', () => {
  const text = readFileSync(resolve(__dirname, '..', 'AGENTS.md'), 'utf8');
  assert.ok(text.includes('docs/production-flow.md'));
  assert.ok(!text.includes('## 分集制作'));
  assert.ok(!text.includes('首帧'));
});

test('agent guide can be used independently of the coordination ledger', () => {
  const guide = readFileSync(resolve(__dirname, '..', 'AGENTS.md'), 'utf8');
  const ledger = readFileSync(resolve(__dirname, 'development-assignments.md'), 'utf8');
  assert.match(ledger, /^# 首版开发分工/m);
  assert.match(ledger, /由协作协调者维护/);
  assert.doesNotMatch(guide, /首版开发分工|development-assignments\.md|\bT\d{2}\b/);
  assert.match(guide, /目标、范围、验收标准和工作分支/);
  assert.match(guide, /git status --short --branch/);
});

test('developer instructions order local validation, draft PR, risk-based review and handoff', () => {
  const text = readFileSync(resolve(__dirname, '..', 'AGENTS.md'), 'utf8');
  const risk = text.split('## 风险级别')[1]?.split('## 开发 Agent')[0];
  assert.ok(risk);
  assert.match(risk, /\*\*高风险\*\*.*调度核心.*数据库迁移.*`contracts\/`.*`\.github\/`.*两个或更多模块.*一个 GPT 会话和一个 Claude 会话/);
  assert.match(risk, /\*\*标准\*\*.*一位审核者，使用与作者不同的模型/);
  const development = text.split('## 开发 Agent')[1]?.split('## 审核 Agent')[0];
  assert.ok(development);
  const steps = [...development.matchAll(/^\d+\. \*\*([^*]+)\*\* (.+)$/gm)];
  assert.deepEqual(steps.map(([, label]) => label), [
    '开发和提交。', '验证并开草稿 PR。', '送审。', '转为正式 PR。',
  ]);
  assert.match(steps[0][2], /编写或更新相关测试、通过对应验证，再创建一个 Git commit/);
  assert.match(steps[0][2], /公开契约和前端类型，请工作台 Agent 核对页面调用/);
  assert.match(steps[1][2], /立即向 `main` 开草稿 PR.*风险级别.*PR CI 是唯一的 CI 门禁/);
  assert.match(steps[2][2], /无需等待 PR CI；审核结论只在同一提交的 PR CI 通过后生效/);
  assert.match(steps[2][2], /提出该意见的审核者复核修复增量/);
  assert.match(steps[3][2], /审核通过且该提交的 PR CI 通过后.*已审核的提交号.*标记为可审阅，即完成交接/);
  assert.match(development, /仅有两个父提交，分别是已审提交和本次合入的远端 `main` 提交/);
  assert.match(development, /git merge-base --is-ancestor "<main 父提交>" origin\/main/);
  assert.match(development, /git merge-tree --write-tree.*命令成功退出.*结果树与.*git rev-parse.*一致时，原审核继续有效/);
  assert.match(development, /自动合并冲突或结果树不一致时，按下面的规则复核增量/);
  assert.match(development, /原审核者复核 `git diff "<已审提交号>\.\.<新提交号>"`/);
  assert.match(development, /headSha.*送审提交一致.*conclusion.*success/);
  assert.match(development, /--event pull_request/);
});

test('reviewer and merge instructions handle missing proof and verify the exact commit', () => {
  const text = readFileSync(resolve(__dirname, '..', 'AGENTS.md'), 'utf8');
  const [review, merge] = text.split('## 审核 Agent')[1].split('## 集成与合并负责人');
  assert.match(review, /git diff "<基线提交号>\.\.\.<待审提交号>"/);
  assert.match(review, /只读，各自给出独立结论/);
  assert.match(review, /审核会话交付 `通过` 或 `阻塞`，附实际审核的提交号/);
  assert.match(review, /git diff "<上次送审提交号>\.\.<当前提交号>"/);
  assert.match(review, /注明审核时该提交 PR CI 的状态/);
  assert.match(review, /无法取得指定提交，报告缺项/);
  assert.match(merge, /北京时间每天 09:00–23:00，每 30 分钟巡检一次.*作者通知时可随时处理.*非草稿 PR/);
  assert.match(merge, /分支保护：只能通过 PR 合入，`quick` 和 `verify` 必须通过，PR 分支必须包含最新 `main`，管理员同样受限/);
  assert.match(merge, /不使用自动合并/);
  assert.doesNotMatch(text, /--auto\b|--disable-auto/);
  assert.match(merge, /最新提交号与风险级别要求的审核证据一致.*前置 PR 已合并/);
  assert.match(merge, /Update branch 更新分支.*有冲突时交回作者/);
  const workflow = readFileSync(resolve(__dirname, '..', '.github/workflows/development.yml'), 'utf8');
  assert.match(workflow, /fetch-depth: 2\s+- name: Record tested PR merge parents\s+if: github.event_name == 'pull_request'/);
  assert.match(workflow, /PR_BASE: \$\{\{ github.event.pull_request.base.sha \}\}/);
  assert.match(workflow, /PR_HEAD: \$\{\{ github.event.pull_request.head.sha \}\}/);
  assert.match(workflow, /printf 'Tested merge parents: %s\\n' "\$parents"\s+test "\$parents" = "\$PR_BASE \$PR_HEAD"/);
  assert.match(merge, /Record tested PR merge parents.*父提交应依次是当前 `main` 和 PR 最新提交/);
  assert.match(merge, /该提交的 PR CI 成功后，立即用 `gh pr merge <PR编号> --merge --match-head-commit <已核验提交号>` 合入/);
});

test('frontend CI invokes Corepack from the workspace that declares pnpm', () => {
  const root = resolve(__dirname, '..');
  const manifest = JSON.parse(readFileSync(resolve(root, 'apps/web/package.json'), 'utf8'));
  const workflow = readFileSync(resolve(root, '.github/workflows/development.yml'), 'utf8');
  const pnpmSteps = workflow.split(/^ {6}- /m).filter(step => /^\s+pnpm /m.test(step));
  assert.match(manifest.packageManager, /^pnpm@\d+\.\d+\.\d+$/);
  assert.ok(pnpmSteps.length > 0);
  for (const step of pnpmSteps) {
    assert.match(step, /^\s+working-directory: apps\/web\s*$/m);
  }
  const readme = readFileSync(resolve(root, 'README.md'), 'utf8');
  assert.doesNotMatch(readme, /pnpm --dir apps\/web/);
  assert.match(readme, /Set-Location apps\/web\r?\ncorepack enable\r?\npnpm install --frozen-lockfile/);
  assert.match(readme, /Set-Location apps\/web\r?\npnpm types:generate/);
});

test('PR CI selects documentation checks or the cached test jobs', () => {
  const root = resolve(__dirname, '..');
  const workflow = readFileSync(resolve(root, '.github/workflows/development.yml'), 'utf8');
  const [triggers, scope, quick, verify] = workflow.split(/^ {2}(?:scope|quick|verify):\s*$/m);
  assert.match(triggers, /^ {2}pull_request:\s*$/m);
  // No merge queue on this repository; a merge_group run would classify paths against HEAD^1 only.
  assert.doesNotMatch(triggers, /merge_group/);
  assert.match(triggers, /push:\s+branches: \[main\]/);
  assert.match(triggers, /cancel-in-progress: \$\{\{ github.event_name == 'pull_request' \}\}/);
  assert.match(scope, /GITHUB_EVENT_NAME" = pull_request/);
  assert.match(scope, /git diff --name-only --no-renames HEAD\^1 HEAD/);
  const pathPattern = scope.match(/! grep -qEv '([^']+)'/)?.[1];
  assert.ok(pathPattern);
  const docPath = new RegExp(pathPattern);
  for (const path of ['README.md', 'AGENTS.md', 'docs/production-flow.md', 'docs/team/guide.md']) {
    assert.match(path, docPath);
  }
  for (const path of ['apps/backend/studio/settings.py', '.github/workflows/development.yml',
    'docs/production-flow.test.cjs', 'docsx/guide.md', 'README.md.exe', '"docs/quoted.md"']) {
    assert.doesNotMatch(path, docPath);
  }
  // Required checks that are skipped count as passing, so a failed scope job must fail both of them.
  assert.match(quick, /needs: scope\s+#[^\n]*\n\s+if: \$\{\{ !cancelled\(\) && needs.scope.outputs.docs-only != 'true' \}\}/);
  assert.match(verify, /needs: scope\s+if: \$\{\{ !cancelled\(\) \}\}/);
  for (const job of [quick, verify]) {
    assert.match(job, /steps:\s+- name: Require path classification\s+if: needs.scope.result != 'success'\s+run: \|[^\n]*\n[^\n]*\n\s+exit 1/);
  }
  assert.match(verify, /name: Check documentation-only PR\s+if: needs.scope.outputs.docs-only == 'true'\s+run: \|\s+git diff --check HEAD\^1 HEAD\s+node --test docs\/production-flow.test.cjs prototype\/prototype.test.cjs/);
  assert.doesNotMatch(quick, /if: github.event_name/);
  assert.match(quick, /image: postgres:18/);
  assert.match(quick, /git diff --check HEAD\^1 HEAD/);
  assert.match(quick, /docker\/bake-action@v6[\s\S]*?targets: api[\s\S]*?api\.cache-from=type=gha,scope=python/);
  assert.match(quick, /docker run --rm --network host.*STUDIO_DB_HOST=127\.0\.0\.1 short-drama-studio-python:dev/);
  assert.match(quick, /^ {12}pytest\s*$/m);
  assert.match(quick, /key: pnpm-.*hashFiles\('apps\/web\/pnpm-lock.yaml'\)/);
  assert.doesNotMatch(quick, /docker compose|test:e2e/);
  const fullPattern = verify.match(/full='([^']+)'/)?.[1];
  assert.ok(fullPattern);
  const fullStack = new RegExp(fullPattern);
  for (const path of ['compose.yaml', 'infra/python.Dockerfile', 'apps/agents/urls.py', 'apps/render/urls.py',
    'workflows/n8n/README.md', 'pyproject.toml', 'uv.lock', '.python-version', '.dockerignore',
    '.github/workflows/development.yml']) {
    assert.match(path, fullStack, path);
  }
  for (const path of ['apps/backend/scheduler/service.py', 'apps/web/src/main.tsx', 'docs/production-flow.test.cjs']) {
    assert.doesNotMatch(path, fullStack, path);
  }
  assert.match(verify, /services=postgres minio api worker web/);
  assert.match(verify, /key: studio-minio-v1-.*hashFiles\('infra\/minio.Dockerfile'\)/);
  assert.match(verify, /docker image load -i/);
  assert.match(verify, /targets: \$\{\{ steps.stack.outputs.targets \}\}/);
  for (const target of ['api', 'render', 'web']) {
    assert.match(verify, new RegExp(String.raw`${target}\.cache-to=type=gha,scope=`));
  }
  assert.match(verify, /name: Start development stack\s+if: needs.scope.outputs.docs-only == 'false' && github.event_name != 'push'\s+run: docker compose up --no-build --wait/);
  assert.match(verify, /name: Check object storage\s+if: needs.scope.outputs.docs-only == 'false' && github.event_name != 'push'\s+run: \|\s+docker compose run --rm --no-deps storage-init\s+docker compose exec -T api pytest apps\/backend\/tests\/test_storage.py/);
  assert.match(verify, /PLAYWRIGHT_CHANNEL: chrome[\s\S]*?pnpm test:e2e/);
  const compose = readFileSync(resolve(root, 'compose.yaml'), 'utf8');
  for (const service of ['minio', 'render', 'web']) {
    assert.match(compose, new RegExp(String.raw`^  ${service}:\s+image: short-drama-studio-${service}:dev$`, 'm'));
  }
  assert.match(verify, /docker image save .* short-drama-studio-minio:dev/);
  const playwright = readFileSync(resolve(root, 'apps/web/playwright.config.ts'), 'utf8');
  assert.match(playwright, /channel: process.env.PLAYWRIGHT_CHANNEL/);
});

test('local documentation links resolve after moving the workflow', () => {
  for (const file of ['AGENTS.md', 'docs/production-flow.md', 'docs/product-design.md', 'docs/technical-design.md', 'docs/scheduler-integration.md']) {
    const path = resolve(__dirname, '..', file);
    const text = readFileSync(path, 'utf8');
    for (const [, target] of text.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
      if (/^https?:/.test(target)) continue;
      assert.ok(existsSync(resolve(dirname(path), target.split('#')[0])), `${file}: ${target}`);
    }
  }
});

test('technical design retains agreed generic lifecycle and equal-weight progress', () => {
  const text = readFileSync(resolve(__dirname, 'technical-design.md'), 'utf8');
  for (const term of ['BaseTask', 'on_processing()', 'on_init()', 'checkpoint', 'child_total', 'task_child_slots', 'task_waits', 'wait_for_children', 'next_step', 'definition_version']) {
    assert.ok(text.includes(term), term);
  }
  assert.ok(text.includes('所有已绑定子任务的 progress 之和 / child_total'));
  assert.ok(!text.includes('10% / 15% / 75%'));
  assert.ok(!text.includes('进度是待产品确认'));
  assert.ok(!text.includes('### 生产步骤与版本依赖'));
  assert.ok(text.includes('[生产流程](production-flow.md)'));
});

test('technical design closes the reviewed scheduling gaps', () => {
  const text = readFileSync(resolve(__dirname, 'technical-design.md'), 'utf8');
  for (const term of ['on_workflow_result(result)', '无副作用', 'max_attempts', 'retry_at', 'blocked_reason', '晚到收养', '/status', 'FFmpeg', 'MinIO', '首版不做', 'child_output']) {
    assert.ok(text.includes(term), term);
  }
  assert.ok(!text.includes('失败或已超时的任务可重试'));
  assert.ok(!text.includes('联调必须验证所选存储的条件写入'));
  assert.ok(!text.includes('return self.finish()'));
});

test('product design exposes retry only when a blocked or timed-out task can retry', () => {
  const text = readFileSync(resolve(__dirname, 'product-design.md'), 'utf8');
  assert.match(text, /失败被阻塞或已超时且允许手动重试时显示「重试」/);
  assert.match(text, /等待供应商核查时显示「待核查供应商任务」/);
  assert.match(text, /等待供应商核查、正常等待、运行或完成的任务隐藏「重试」/);
  assert.match(text, /点击「重试」/);
  assert.ok(text.includes('自动重试'));
});
