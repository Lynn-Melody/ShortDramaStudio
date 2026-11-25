'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { makeProject, progress, projectState, ready, taskOutputs, libraryAssets, versionGroups, visibleTasks, graphLayout, fitGraph } = require('./prototype.js');

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
  assert.equal(progress(project), 68);
  assert.equal(projectState(project)[0], '运行中');
  project.paused = true;
  assert.equal(projectState(project)[0], '已暂停');
  for (const task of project.tasks) task.state = 'complete';
  assert.equal(progress(project), 100);
  assert.equal(projectState(project)[0], '已完成');
});

test('an active task may finish during pause without releasing its dependent task', () => {
  const project = makeProject(1, '午夜来电');
  const voice = project.tasks.find(task => task.id === 'voice2');
  project.paused = true;
  voice.state = 'complete';
  assert.equal(progress(project), 71);
  assert.deepEqual(ready(project), []);
  assert.equal(project.tasks.find(task => task.id === 'video2').state, 'waiting');
  project.paused = false;
  assert.deepEqual(ready(project).map(task => task.id), ['video2']);
});

test('a new project has a waiting root and starts with planning', () => {
  const project = makeProject(1, '新项目', 'new');
  assert.equal(progress(project), 0);
  assert.equal(project.tasks[0].state, 'waiting');
  assert.equal(project.tasks[0].name, '短剧制作');
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

test('the running tree merges shared ancestors and keeps its root when nothing runs', () => {
  const { tasks } = makeProject(1, '午夜来电');
  assert.deepEqual(visibleTasks(tasks).map(task => task.id), ['root', 'episodes', 'ep2', 'av2', 'voice2', 'silent2']);
  for (const task of tasks.filter(task => task.state === 'running')) task.state = 'complete';
  assert.deepEqual(visibleTasks(tasks).map(task => task.id), ['root']);
});

test('the selected subtree includes children and grandchildren across every state', () => {
  const { tasks } = makeProject(1, '午夜来电');
  const visible = visibleTasks(tasks, 'ep2');
  assert.deepEqual([...new Set(visible.map(task => task.state))].sort(), ['complete', 'running', 'timeout', 'waiting']);
  assert.ok(visible.some(task => task.id === 'voice2'));
  assert.ok(!visibleTasks(tasks, 'episodes').some(task => task.id === 'voice2'));
  assert.deepEqual(visibleTasks(tasks, 'voice2').map(task => task.id), ['voice2']);
});

test('the narrow task canvas fits all visible circles without clipping', () => {
  const { tasks } = makeProject(1, '午夜来电');
  const width = 445;
  const height = 530;
  for (const subtree of [null, 'root', 'ep2', 'voice2']) {
    const graph = graphLayout(tasks, subtree);
    const view = fitGraph(graph, width, height);
    for (const node of graph.nodes) {
      assert.ok(view.x + (node.x - 15) * view.zoom >= 0);
      assert.ok(view.y + (node.y - 15) * view.zoom >= 0);
      assert.ok(view.x + (node.x + 15) * view.zoom <= width);
      assert.ok(view.y + (node.y + 15) * view.zoom <= height);
    }
  }
});

test('asset scope and keyword search can be combined', () => {
  const project = makeProject(1, '午夜来电');
  const episode = { name: '雨夜电话亭', episode: '1', shot: '01', type: '分镜' };
  const common = { name: '电话亭设置', episode: 'common', type: '角色、场景与道具' };
  project.assets.push(episode, common);
  assert.deepEqual(libraryAssets(project, { type: '全部', query: ' 电话亭 ' }), [episode, common]);
  assert.deepEqual(libraryAssets(project, { type: '全部', query: '电话亭', episode: 'common' }), [common]);
  assert.deepEqual(libraryAssets(project, { type: '分镜', query: '电话亭', episode: '1', shot: '01' }), [episode]);
});

test('delivery filtering includes the episode video, captions, and editable timeline', () => {
  const project = makeProject(1, '午夜来电');
  const delivery = ['视频', '交付', '交付'].map(type => ({ task: 'deliver', type, episode: '1' }));
  project.assets.push(...delivery, { task: 'video2', type: '视频', episode: '2' });
  assert.deepEqual(libraryAssets(project, { type: '交付', episode: '1' }), delivery);
});
