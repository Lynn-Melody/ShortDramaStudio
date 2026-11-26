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
  assert.ok(text.includes('Git commit'));
  assert.ok(text.includes('测试和验证全部通过'));
  assert.ok(!text.includes('## 分集制作'));
  assert.ok(!text.includes('首帧'));
});

test('local documentation links resolve after moving the workflow', () => {
  for (const file of ['AGENTS.md', 'docs/production-flow.md', 'docs/product-design.md', 'docs/technical-design.md']) {
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
