'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');

// Documentation checks only; runtime scheduling tests belong to the future backend.
for (const file of ['AGENTS.md', 'docs/product-design.md', 'docs/technical-design.md']) {
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
