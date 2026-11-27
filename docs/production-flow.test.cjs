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

test('developer instructions order local validation, commit, branch CI, dual review and PR handoff', () => {
  const text = readFileSync(resolve(__dirname, '..', 'AGENTS.md'), 'utf8');
  const development = text.split('## 开发 Agent')[1]?.split('## 审核 Agent')[0];
  assert.ok(development);
  const steps = [...development.matchAll(/^\d+\. \*\*([^*]+)\*\* (.+)$/gm)];
  assert.deepEqual(steps.map(([, label]) => label), [
    '开发和提交。', '验证完整任务。', '送审。', '开 PR。',
  ]);
  assert.match(steps[0][2], /编写或更新相关测试、通过对应验证，再创建一个 Git commit/);
  assert.match(steps[1][2], /任务分支推送.*最终提交号查找对应的 GitHub Actions 分支 CI/);
  assert.match(steps[2][2], /分支 CI 通过后.*一个 GPT 会话和一个 Claude 会话.*独立只读审核/);
  assert.match(steps[3][2], /双审通过后.*已审核的提交号.*提交 PR 即完成交接/);
  assert.match(development, /headSha.*送审提交一致.*conclusion.*success/);
});

test('reviewer and merge instructions handle missing proof and verify the exact commit', () => {
  const text = readFileSync(resolve(__dirname, '..', 'AGENTS.md'), 'utf8');
  const [review, merge] = text.split('## 审核 Agent')[1].split('## 集成与合并负责人');
  assert.match(review, /git diff "<基线提交号>\.\.\.<待审提交号>"/);
  assert.match(review, /只读，各自给出独立结论/);
  assert.match(review, /审核会话交付 `通过` 或 `阻塞`，附实际审核的提交号/);
  assert.match(review, /无法取得指定提交或同一提交的成功 CI，报告缺项/);
  assert.match(merge, /北京时间每天 09:00–23:00，每 30 分钟巡检一次.*非草稿 PR/);
  assert.match(merge, /最新提交号与两份审核证据一致.*PR 集成 CI 通过/);
  assert.match(merge, /--match-head-commit <已审提交号>/);
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

test('product design exposes manual retry for blocked or timed-out tasks', () => {
  const text = readFileSync(resolve(__dirname, 'product-design.md'), 'utf8');
  assert.match(text, /失败被阻塞或已超时时显示「重试」/);
  assert.match(text, /点击「重试」/);
  assert.ok(text.includes('自动重试'));
});
