'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { makeProject, progress, projectState, ready, taskOutputs, libraryAssets, versionGroups } = require('./prototype.js');

test('the standalone entry page loads its stylesheet and script from the same folder', () => {
  const html = readFileSync(join(__dirname, 'index.html'), 'utf8');
  assert.match(html, /<link rel="stylesheet" href="prototype\.css">/);
  assert.match(html, /<script src="prototype\.js" defer><\/script>/);
  for (const asset of ['prototype.css', 'prototype.js']) {
    assert.ok(readFileSync(join(__dirname, asset)).length > 0);
  }
});

test('progress reflects completed nodes and project status', () => {
  const project = makeProject(1, '午夜来电');
  assert.equal(progress(project), 50);
  assert.equal(projectState(project)[0], '运行中');
  project.paused = true;
  assert.equal(projectState(project)[0], '已暂停');
  for (const task of project.tasks) task.state = 'complete';
  assert.equal(progress(project), 100);
  assert.equal(projectState(project)[0], '已完成');
});

test('an active task may finish during pause without releasing its dependent task', () => {
  const project = makeProject(1, '午夜来电');
  const board = project.tasks.find(task => task.id === 'board2');
  project.paused = true;
  board.state = 'complete';
  assert.equal(progress(project), 58);
  assert.deepEqual(ready(project), []);
  assert.equal(project.tasks.find(task => task.id === 'video2').state, 'waiting');
  project.paused = false;
  assert.deepEqual(ready(project).map(task => task.id), ['video2']);
});

test('a new project starts with the root task ready', () => {
  const project = makeProject(1, '新项目', 'new');
  assert.equal(progress(project), 0);
  assert.deepEqual(ready(project).map(task => task.id), ['idea']);
});

test('task output preserves exact versions and works independently of library filters', () => {
  const project = makeProject(1, '午夜来电');
  const old = { task: 'board1', group: 's01', type: '分镜', episode: '1', shot: '01', version: 1, adopted: true };
  const latest = { ...old, task: 'redo', version: 2, adopted: false };
  const sound = { task: 'board1', group: 'sound', type: '音频', episode: 'common', version: 1 };
  project.assets.push(old, latest, sound);
  const filters = { type: '音频', episode: 'common', shot: '' };

  assert.deepEqual(libraryAssets(project, filters), [sound]);
  assert.deepEqual(taskOutputs(project, 'board1'), [old, sound]);
  assert.deepEqual(libraryAssets(project, filters), [sound]);
  assert.deepEqual(taskOutputs(project, 'missing'), []);
  assert.deepEqual(versionGroups(project.assets), [[sound], [latest, old]]);
  assert.equal(versionGroups(project.assets)[1][1].adopted, true);
  assert.deepEqual(libraryAssets(project, { type: '全部', episode: '1', shot: '01' }), [old, latest]);
});

test('the narrow task canvas fits all nodes at readable text size', () => {
  const { graphLayout, fitGraph } = require('./prototype.js');
  const project = makeProject(1, '午夜来电');
  const width = 445;
  const height = 530;
  const graph = graphLayout(project.tasks, width);
  const view = fitGraph(graph, width, height);

  assert.equal(graph.compact, true);
  assert.equal(graph.nodes.length, project.tasks.length);
  assert.ok(graph.fontSize * view.zoom >= 11);
  assert.ok(12 * view.zoom >= 11);
  for (const node of graph.nodes) {
    assert.ok(view.x + node.x * view.zoom >= 0);
    assert.ok(view.y + node.y * view.zoom >= 0);
    assert.ok(view.x + (node.x + graph.nodeWidth) * view.zoom <= width);
    assert.ok(view.y + (node.y + graph.nodeHeight) * view.zoom <= height);
  }
  assert.equal(graphLayout(project.tasks, 860).compact, false);
});
