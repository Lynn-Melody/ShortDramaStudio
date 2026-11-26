'use strict';

const states = { waiting: '等待', running: '运行', timeout: '超时', complete: '完成' };
const types = ['全部', '剧本', '角色、场景与道具', '分镜', '音频', '视频', '交付'];
const blueprint = [
  ['root', '短剧制作', null, [], 'waiting'],
  ['plan', '全剧规划', 'root', [], 'complete'],
  ['idea', '创意与世界观', 'plan', [], 'complete'],
  ['outline', '分集大纲', 'plan', ['idea'], 'complete'],
  ['global', '全局关键素材生成', 'root', ['plan'], 'complete'],
  ['cast', '人物与场景', 'global', ['plan'], 'complete'],
  ['props', '关键道具', 'global', ['plan'], 'complete'],
  ['episodes', '全部剧集制作', 'root', ['global'], 'waiting'],
  ['ep1', '第 1 集制作', 'episodes', ['global'], 'complete'],
  ['script', '制作分集剧本', 'ep1', ['global'], 'complete'],
  ['structure1', '分集剧本结构化', 'ep1', ['script'], 'complete'],
  ['key1', '分集关键素材制作', 'ep1', ['structure1'], 'complete'],
  ['board1', '制作分集全部分镜图', 'ep1', ['key1'], 'complete'],
  ['av1', '生成所有分镜视频及音频', 'ep1', ['board1'], 'complete'],
  ['voice', '角色试音', 'av1', ['board1'], 'complete'],
  ['video', '第 1 集镜头生成', 'av1', ['voice'], 'complete'],
  ['deliver', '剪辑与合成', 'ep1', ['av1'], 'complete'],
  ['ep2', '第 2 集制作', 'episodes', ['ep1'], 'waiting'],
  ['script2', '制作分集剧本', 'ep2', ['ep1'], 'complete'],
  ['structure2', '分集剧本结构化', 'ep2', ['script2'], 'complete'],
  ['key2', '分集关键素材制作', 'ep2', ['structure2'], 'complete'],
  ['board2', '制作分集全部分镜图', 'ep2', ['key2'], 'complete'],
  ['av2', '生成所有分镜视频及音频', 'ep2', ['board2'], 'waiting'],
  ['voice2', '镜头 01 · 对白配音', 'av2', ['board2'], 'running'],
  ['video2', '镜头 01 · 视频生成', 'av2', ['voice2'], 'waiting'],
  ['silent2', '镜头 02 · 视频生成', 'av2', ['board2'], 'running'],
  ['sound', '环境音与动作音效', 'av2', ['board2'], 'timeout'],
  ['deliver2', '剪辑与合成', 'ep2', ['av2'], 'waiting'],
];

const rootId = 'root';

function visibleTasks(tasks, subtree) {
  if (subtree) {
    const ids = new Set([subtree]);
    let generation = [subtree];
    for (let depth = 0; depth < 2; depth++) {
      generation = tasks.filter(task => generation.includes(task.parent)).map(task => task.id);
      generation.forEach(id => ids.add(id));
    }
    return tasks.filter(task => ids.has(task.id));
  }

  const lookup = new Map(tasks.map(task => [task.id, task]));
  const ids = new Set([rootId]);
  for (const task of tasks.filter(item => item.state === 'running')) {
    for (let current = task; current && !ids.has(current.id); current = lookup.get(current.parent)) {
      ids.add(current.id);
    }
  }
  return tasks.filter(task => ids.has(task.id));
}

function graphLayout(tasks, subtree) {
  const visible = visibleTasks(tasks, subtree);
  const nodes = [];
  let leaf = 0;
  const rootY = subtree ? 62 : 50;
  function position(task, depth) {
    const node = { ...task, x: 0, y: rootY + depth * 69 };
    nodes.push(node);
    const children = visible.filter(child => child.parent === task.id);
    const centers = children.map(child => position(child, depth + 1));
    node.x = centers.length ? (centers[0] + centers.at(-1)) / 2 : 50 + leaf++ * 69;
    return node.x;
  }
  position(visible[0], 0);
  return {
    nodes,
    back: subtree ? { x: nodes[0].x, y: rootY - 42 } : null,
    width: 100 + (leaf - 1) * 69,
    height: 50 + Math.max(...nodes.map(node => node.y)),
  };
}

function fitGraph(layout, width, height) {
  const zoom = Math.min(1.55, Math.max(.2, Math.min((width - 24) / layout.width, (height - 48) / layout.height)));
  return { zoom, x: (width - layout.width * zoom) / 2, y: (height - layout.height * zoom) / 2 - 5 };
}

function taskDetailSide(node, view, viewportHeight) {
  return node && view.y + node.y * view.zoom > viewportHeight / 2 ? 'top' : 'bottom';
}

function makeProject(id, name, mode = 'sample') {
  const tasks = blueprint.map(([id, name, parent, deps, sample]) => ({
    id, name, parent, deps,
    state: mode === 'sample' ? sample : mode === 'complete' ? 'complete' : 'waiting',
  }));
  return { id, name, paused: mode === 'paused', tasks, assets: [] };
}

function progress(project) {
  return Math.round(100 * project.tasks.filter(task => task.state === 'complete').length / project.tasks.length);
}

function projectState(project) {
  if (progress(project) === 100) return ['已完成', 'complete'];
  if (project.paused) return ['已暂停', 'waiting'];
  return ['运行中', 'running'];
}

function ready(project) {
  if (project.paused) return [];
  return project.tasks.filter(task => task.state === 'waiting' && !project.tasks.some(child => child.parent === task.id) &&
    task.deps.every(dep => project.tasks.some(candidate => candidate.id === dep && candidate.state === 'complete')));
}

function retryTask(project, taskId) {
  const task = project.tasks.find(candidate => candidate.id === taskId);
  if (!task || task.state !== 'timeout') return false;
  task.state = 'waiting';
  return true;
}

const taskOutputs = (project, taskId) => project.assets.filter(asset => asset.task === taskId);
const libraryAssets = (project, filters) => project.assets.filter(asset =>
  (filters.type === types[0] || asset.type === filters.type ||
    (filters.type === '交付' && /^deliver/.test(asset.task || ''))) &&
  (!filters.episode || asset.episode === filters.episode) &&
  (!filters.shot || asset.shot === filters.shot) &&
  (!filters.query || asset.name.toLocaleLowerCase().includes(filters.query.trim().toLocaleLowerCase())));
function currentEpisode(project) {
  const episodes = project.tasks.filter(task => /^ep\d+$/.test(task.id));
  return (episodes.find(task => task.state !== 'complete') || episodes.at(-1))?.id.slice(2) || '';
}

const latestFirst = assets => assets.slice().reverse();
function versionGroups(assets) {
  const groups = new Map();
  for (const asset of latestFirst(assets)) {
    if (!groups.has(asset.group)) groups.set(asset.group, []);
    groups.get(asset.group).push(asset);
  }
  return [...groups.values()];
}

if (typeof module !== 'undefined') {
  module.exports = { makeProject, progress, projectState, ready, retryTask, taskOutputs, libraryAssets, versionGroups, visibleTasks, graphLayout, fitGraph, taskDetailSide };
}

if (typeof document !== 'undefined') mount();

function mount() {
  const element = id => document.getElementById(id);
  const themeToggle = element('theme-toggle');
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    themeToggle.textContent = theme === 'dark' ? '☀ 明亮' : '☾ 深色';
    themeToggle.setAttribute('aria-label', theme === 'dark' ? '切换明亮主题' : '切换深色主题');
  }
  try {
    applyTheme(localStorage.getItem('studio-theme') === 'dark' ? 'dark' : 'light');
  } catch {
    applyTheme('light');
  }
  themeToggle.addEventListener('click', () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(theme);
    try { localStorage.setItem('studio-theme', theme); } catch {}
  });
  const text = (id, value) => { element(id).textContent = value; };
  const create = (tag, className, value) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value) item.textContent = value;
    return item;
  };
  const button = (label, className, action) => {
    const control = create('button', className, label);
    control.type = 'button';
    control.addEventListener('click', action);
    return control;
  };
  const state = (label, tone) => create('span', 'state ' + tone, label);
  const episodeName = episode => episode === 'common' ? '全剧共用' : '第 ' + episode + ' 集';
  const covers = ['#526079', '#613e5b', '#385879', '#855b4a', '#353e60', '#55536d'];
  const sample = makeProject(1, '午夜来电');
  function asset(task, group, name, type, episode, shot, version, adopted, content) {
    return { task, group, name, type, episode, shot, version, adopted, content };
  }
  sample.assets.push(
    asset('outline', 'outline', '分集大纲', '剧本', 'common', '', 1, true, '深夜，一部早已停机的手机突然响起。来电那头是一通尚未发生的求救。'),
    asset('cast', 'lead', '林夏 · 角色设定', '角色、场景与道具', 'common', '', 1, true),
    asset('cast', 'room', '电话亭 · 场景设定', '角色、场景与道具', 'common', '', 1, true),
    asset('props', 'phone', '停机手机 · 道具设定', '角色、场景与道具', 'common', '', 1, true),
    asset('voice', 'lead-voice', '林夏 · 试音', '音频', 'common', '', 1, false),
    asset('script', 'script1', '第 1 集 · 失效号码', '剧本', '1', '', 1, true, '外景 · 雨夜。林夏经过电话亭，雨声里响起一段熟悉的铃声。'),
    asset('script2', 'script2', '第 2 集 · 空白录音', '剧本', '2', '', 1, true, '内景 · 清晨。林夏反复切换录音轨道，终于听见了一句低语。'),
    asset('board1', 's01', '雨夜电话亭', '分镜', '1', '01', 1, true),
    asset('board1', 's01', '雨夜电话亭', '分镜', '1', '01', 2, false),
    asset('board1', 's02', '接起电话', '分镜', '1', '02', 1, true),
    asset('board1', 's03', '玻璃倒影', '分镜', '1', '03', 1, false),
    asset('video', 'scene1', '雨夜电话亭 · 镜头视频', '视频', '1', '01', 1, true),
    asset('deliver', 'final1', '第 1 集 · 成片', '视频', '1', '', 1, true),
    asset('deliver', 'captions1', '第 1 集 · 字幕', '交付', '1', '', 1, true, '00:00:02.400 → 00:00:04.800　你终于接电话了。'),
    asset('deliver', 'timeline1', '第 1 集 · 可编辑时间轴', '交付', '1', '', 1, true, '镜头 01 → 镜头 02 → 镜头 03 · 对白、音效与字幕已对齐。'),
    asset('board2', 's21', '清晨回放', '分镜', '2', '01', 1, true),
    asset('board2', 's22', '录音波峰', '分镜', '2', '02', 1, true),
  );
  const second = makeProject(2, '雾都回信', 'paused');
  for (const task of second.tasks.filter(task => ['plan', 'idea', 'outline'].includes(task.id))) task.state = 'complete';
  second.assets.push(asset('outline', 'outline', '雾都回信 · 分集大纲', '剧本', 'common', '', 1, true, '每当雾起，一封寄往过去的信就会出现在门前。'));
  const third = makeProject(3, '第七次日落', 'complete');
  third.assets.push(asset('deliver2', 'final', '第七次日落 · 成片', '视频', '2', '', 1, true));
  const projects = [sample, second, third];
  const timers = new Map();
  let project = sample;
  let currentTask = null;
  let subtree = null;
  const graphHistory = [];
  let outputTask = null;
  let filters = { type: types[0], episode: currentEpisode(project), shot: '', query: '' };
  let savedScroll = 0;
  let view = { x: 0, y: 0, zoom: 1 };
  let drag;
  let autoFit = true;
  let graph = graphLayout(project.tasks, subtree);
  let viewportSize;
  let focusIndex = {};
  const viewport = element('viewport');
  const list = element('asset-list');
  const detail = element('task-detail');
  const assetPanel = document.querySelector('.assets');
  const libraryDialog = element('library-dialog');
  const expandAssets = element('expand-assets');
  const assetSlot = document.createComment('asset panel position');
  assetPanel.before(assetSlot);
  expandAssets.addEventListener('click', () => {
    if (libraryDialog.open) {
      libraryDialog.close();
      return;
    }
    const scroll = list.scrollTop;
    libraryDialog.append(assetPanel);
    expandAssets.textContent = '收起';
    libraryDialog.showModal();
    list.scrollTop = scroll;
  });
  libraryDialog.addEventListener('close', () => {
    const scroll = list.scrollTop;
    assetSlot.after(assetPanel);
    expandAssets.textContent = '展开';
    list.scrollTop = scroll;
    expandAssets.focus({ preventScroll: true });
  });

  function renderProjects() {
    const items = projects.map(item => {
      const itemButton = button('', 'project-item', () => selectProject(item));
      const [label, tone] = projectState(item);
      itemButton.setAttribute('aria-pressed', String(item === project));
      const meta = create('span', 'project-meta');
      meta.append(state(label, tone), create('span', '', progress(item) + '%'));
      itemButton.append(create('span', 'project-name', item.name), meta);
      return itemButton;
    });
    element('projects').replaceChildren(...items);
  }

  function renderHeader() {
    const [label, tone] = projectState(project);
    text('project-title', project.name);
    element('project-status').replaceChildren(state(label, tone));
    text('progress-text', progress(project) + '%');
    element('progress').value = progress(project);
    const timeouts = project.tasks.filter(task => task.state === 'timeout').length;
    const running = project.tasks.filter(task => task.state === 'running').length;
    const summary = [];
    if (project.paused && running) summary.push(running + ' 个运行中的任务正在收尾');
    if (timeouts) summary.push(timeouts + ' 个任务超时');
    text('project-summary', summary.join(' · '));
    element('project-summary').hidden = summary.length === 0;
    const toggle = element('toggle-project');
    toggle.hidden = label === '已完成';
    toggle.textContent = project.paused ? '▶ 继续' : 'Ⅱ 暂停';
  }

  function selectProject(item) {
    if (project !== item) {
      project = item;
      currentTask = null;
      subtree = null;
      graphHistory.length = 0;
      outputTask = null;
      filters = { type: types[0], episode: currentEpisode(project), shot: '', query: '' };
      savedScroll = 0;
      focusIndex = {};
    }
    renderProjects();
    renderHeader();
    renderGraph();
    renderAssets(0);
    fit();
  }

  function renderGraph() {
    graph = graphLayout(project.tasks, subtree);
    const active = project.tasks.find(task => task.id === currentTask);
    text('graph-title', subtree ? project.tasks.find(task => task.id === subtree).name : '运行路径');
    text('task-count', graph.nodes.length + ' 个节点');
    const back = element('graph-back');
    back.hidden = !graph.back;
    if (graph.back) {
      back.style.left = graph.back.x + 'px';
      back.style.top = graph.back.y + 'px';
    }
    element('find-running').disabled = !graph.nodes.some(task => task.state === 'running');
    element('find-timeout').disabled = !project.tasks.some(task => task.state === 'timeout');
    const nodes = graph.nodes.map(task => {
      const node = button('', 'node ' + task.state, event => {
        if (event.detail === 1 || event.detail === 0) showTask(task.id);
      });
      node.dataset.task = task.id;
      node.setAttribute('aria-label', task.name + '，' + states[task.state]);
      node.setAttribute('aria-pressed', String(task.id === currentTask));
      node.style.left = task.x + 'px';
      node.style.top = task.y + 'px';
      node.append(create('span', 'node-tip', task.name));
      node.addEventListener('dblclick', () => enterTree(task.id));
      node.addEventListener('keydown', event => {
        if (event.key !== 'ArrowRight') return;
        event.preventDefault();
        enterTree(task.id);
      });
      return node;
    });
    element('nodes').replaceChildren(...nodes);
    const paths = graph.nodes.flatMap(task => {
      const parent = graph.nodes.find(candidate => candidate.id === task.parent);
      if (!parent) return [];
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      const mid = (task.y + parent.y) / 2;
      const coordinates = 'M ' + parent.x + ' ' + parent.y + ' C ' + parent.x + ' ' + mid +
        ', ' + task.x + ' ' + mid + ', ' + task.x + ' ' + task.y;
      path.setAttribute('d', coordinates);
      path.setAttribute('class', 'edge');
      return [path];
    });
    element('connections').replaceChildren(...paths);
    renderDetail(active);
  }

  function enterTree(id) {
    if (subtree === id) return;
    graphHistory.push({ subtree, view: { ...view }, autoFit });
    subtree = id;
    currentTask = null;
    renderGraph();
    fit();
    focusNode(id);
  }

  element('graph-back').addEventListener('click', () => {
    const previous = graphHistory.pop();
    const previousRoot = subtree;
    subtree = previous.subtree;
    view = previous.view;
    autoFit = previous.autoFit;
    currentTask = null;
    renderGraph();
    updateView();
    focusNode(previousRoot);
  });

  function renderDetail(task) {
    detail.hidden = !task;
    if (!task) return;
    const node = graph.nodes.find(candidate => candidate.id === task.id);
    detail.dataset.side = taskDetailSide(node, view, viewport.clientHeight);
    const heading = create('div', 'detail-heading');
    const close = button('×', '', () => {
      showTask(null);
      focusNode(task.id);
    });
    close.setAttribute('aria-label', '关闭任务详情');
    heading.append(create('strong', '', task.name), close);
    const messages = {
      waiting: '前置任务：' + (task.deps.map(id => project.tasks.find(dep => dep.id === id).name).join('、') || '无'),
      running: project.paused ? '当前运行将在暂停后收尾。' : '正在处理此任务。',
      timeout: '生成任务超过等待时限。',
      complete: '此任务已完成。',
    };
    detail.replaceChildren(heading, create('span', task.state, states[task.state]), create('p', '', messages[task.state]));
    if (task.state === 'timeout') detail.append(button('重试', '', () => {
      if (!retryTask(project, task.id)) return;
      refresh(project);
      showTask(task.id, true);
    }));
    const count = taskOutputs(project, task.id).length;
    if (count) detail.append(button('查看 ' + count + ' 个产出', 'primary', () => {
      if (!outputTask) savedScroll = list.scrollTop;
      outputTask = task.id;
      renderAssets(0);
      element('asset-controls').querySelector('button').focus({ preventScroll: true });
    }));
  }

  function focusNode(id) {
    element('nodes').querySelector('[data-task="' + id + '"]')?.focus({ preventScroll: true });
  }

  function showTask(id, focus) {
    currentTask = id;
    element('nodes').querySelectorAll('.node').forEach(node => {
      node.setAttribute('aria-pressed', String(node.dataset.task === id));
    });
    renderDetail(project.tasks.find(task => task.id === id));
    if (focus) focusNode(id);
  }

  function updateView() {
    element('world').style.transform = 'translate(' + view.x + 'px, ' + view.y + 'px) scale(' + view.zoom + ')';
    text('zoom-level', Math.round(view.zoom * 100) + '%');
  }

  function fit() {
    autoFit = true;
    view = fitGraph(graph, viewport.clientWidth, viewport.clientHeight);
    updateView();
  }

  function zoomAt(factor, x, y) {
    autoFit = false;
    const zoom = Math.max(.24, Math.min(2, view.zoom * factor));
    const ratio = zoom / view.zoom;
    view = { zoom, x: x - (x - view.x) * ratio, y: y - (y - view.y) * ratio };
    updateView();
  }

  function locate(status) {
    let matching = graph.nodes.filter(task => task.state === status);
    if (!matching.length) {
      const target = project.tasks.find(task => task.state === status);
      if (!target) return;
      enterTree(target.parent);
      matching = graph.nodes.filter(task => task.state === status);
    }
    focusIndex[status] = ((focusIndex[status] ?? -1) + 1) % matching.length;
    const task = matching[focusIndex[status]];
    autoFit = false;
    view.zoom = Math.max(view.zoom, .87);
    view.x = viewport.clientWidth / 2 - task.x * view.zoom;
    view.y = viewport.clientHeight / 2 - task.y * view.zoom;
    showTask(task.id, true);
    updateView();
  }

  function renderAssets(scroll = list.scrollTop) {
    const controls = element('asset-controls');
    let items;
    if (outputTask) {
      const visible = latestFirst(taskOutputs(project, outputTask));
      const header = create('div', 'output-heading');
      const back = button('← 返回素材库', '', () => {
        outputTask = null;
        renderAssets(savedScroll);
        controls.querySelector('[aria-pressed="true"]').focus({ preventScroll: true });
      });
      header.append(back, create('strong', '', project.tasks.find(task => task.id === outputTask).name));
      controls.replaceChildren(header);
      items = visible.map(item => assetCard([item]));
      text('asset-count', visible.length + ' 个产出');
    } else {
      const tabList = create('div', 'asset-types');
      for (const type of types) {
        const tab = button(type, '', () => {
          filters.type = type;
          renderAssets(0);
          controls.querySelector('[aria-pressed="true"]').focus({ preventScroll: true });
        });
        tab.setAttribute('aria-pressed', String(filters.type === type));
        tabList.append(tab);
      }
      const selectors = create('div', 'filters');
      const episodeChoices = [...new Set(project.tasks.filter(task => /^ep\d+$/.test(task.id)).map(task => task.id.slice(2)))].sort();
      const current = currentEpisode(project);
      const search = create('input', 'asset-search');
      search.type = 'search';
      search.placeholder = '搜索素材名称';
      search.value = filters.query;
      search.setAttribute('aria-label', '搜索素材名称');
      search.addEventListener('input', () => {
        filters.query = search.value;
        renderLibraryItems(0);
      });
      const shotChoices = [...new Set(project.assets.filter(item => !filters.episode || item.episode === filters.episode)
        .map(item => item.shot).filter(Boolean))].sort();
      selectors.append(
        selectFilter('集数', 'episode', [['', '全项目'], ['common', '全剧共用'], ...episodeChoices.map(value => [value, value === current ? '当前集 · ' + episodeName(value) : episodeName(value)])]),
        selectFilter('镜头', 'shot', [['', '所有镜头'], ...shotChoices.map(value => [value, '镜头 ' + value])]),
      );
      controls.replaceChildren(search, tabList, selectors);
      renderLibraryItems(scroll);
      return;
    }
    setAssetItems(items, scroll);
  }

  function renderLibraryItems(scroll) {
    const items = versionGroups(libraryAssets(project, filters)).map(assetCard);
    text('asset-count', items.length + ' 项素材');
    setAssetItems(items, scroll);
  }

  function setAssetItems(items, scroll) {
    list.replaceChildren(...(items.length ? items : [create('p', 'empty', project.assets.length ? '当前筛选下没有素材' : '素材生成后会显示在这里')]));
    list.scrollTop = scroll;
  }

  function selectFilter(label, key, choices) {
    const select = create('select');
    select.id = 'filter-' + key;
    select.setAttribute('aria-label', label);
    for (const [value, name] of choices) {
      const option = create('option', '', name);
      option.value = value;
      select.append(option);
    }
    select.value = filters[key];
    select.disabled = choices.length < 2;
    select.addEventListener('change', () => {
      filters[key] = select.value;
      if (key === 'episode') filters.shot = '';
      renderAssets(0);
      element(select.id).focus({ preventScroll: true });
    });
    return select;
  }

  function assetCard(versions) {
    const [item] = versions;
    const card = create('article', 'asset-card');
    const main = button('', 'asset-main', () => preview(item));
    main.append(cover(item), create('span', 'asset-title', item.name), assetMeta(item));
    card.append(main);
    if (versions.length > 1) {
      const details = create('details');
      details.append(create('summary', '', versions.length + ' 个版本'));
      versions.forEach(version => {
        const choice = button('V' + version.version + (version.adopted ? ' · 已采用' : ''), 'version', () => preview(version));
        details.append(choice);
      });
      card.append(details);
    }
    return card;
  }

  function assetMeta(item) {
    const meta = create('span', 'asset-meta');
    meta.append(create('span', '', episodeName(item.episode)));
    if (item.shot) meta.append(create('span', '', '镜头 ' + item.shot));
    meta.append(create('span', '', 'V' + item.version));
    if (item.adopted) meta.append(create('span', 'adopted', '已采用'));
    return meta;
  }

  function cover(item) {
    const coverType = item.type === '剧本' ? ' text' : item.type === '音频' ? ' audio' : '';
    const frame = create('span', 'asset-cover' + coverType);
    if (item.group === 's21' || item.group === 's22') {
      const frameArt = create('span', 'frame-art ' + (item.group === 's21' ? 'morning-scene' : 'wave-scene'));
      frameArt.setAttribute('aria-hidden', 'true');
      frameArt.append(create('i', 'art-window'), create('i', 'art-subject'), create('i', 'art-prop'));
      frame.append(frameArt);
    }
    const tone = [...item.group].reduce((sum, letter) => sum + letter.charCodeAt(0), item.version) % covers.length;
    frame.style.setProperty('--cover', 'radial-gradient(ellipse at 25% 10%, ' + covers[tone] + ', #232337 87%)');
    frame.append(create('span', '', item.type));
    return frame;
  }

  function preview(item) {
    const heading = create('div', 'dialog-heading');
    const close = button('×', '', () => element('asset-dialog').close());
    close.setAttribute('aria-label', '关闭预览');
    heading.append(create('h2', '', item.name), close);
    const body = create('div', 'preview-body');
    body.append(cover(item), assetMeta(item));
    if (item.content) body.append(create('p', '', item.content));
    element('asset-preview').replaceChildren(heading, body);
    element('asset-dialog').showModal();
  }

  function simulateCompletion(item, task) {
    const key = item.id + ':' + task.id;
    if (timers.has(key)) return;
    const timer = setTimeout(() => {
      timers.delete(key);
      task.state = 'complete';
      if (task.id === 'board2') {
        item.assets.push(
          asset(task.id, 's21', '清晨回放', '分镜', '2', '01', 1, false),
          asset(task.id, 's22', '录音波峰', '分镜', '2', '02', 1, false),
        );
      }
      if (['video', 'video2', 'silent2'].includes(task.id)) {
        const episode = task.id === 'video' ? '1' : '2';
        item.assets.push(asset(task.id, task.id, episodeName(episode) + ' · 镜头片段', '视频', episode, task.id === 'silent2' ? '02' : '01', 1, false));
      }
      if (task.id === 'voice2') item.assets.push(asset(task.id, task.id, '镜头 01 · 对白配音', '音频', '2', '01', 1, true));
      if (task.id === 'outline' && item.idea) {
        item.assets.push(asset(task.id, 'outline', '分集大纲', '剧本', 'common', '', 1, false, item.idea));
      }
      for (const parent of item.tasks.slice().reverse()) {
        const children = item.tasks.filter(child => child.parent === parent.id);
        if (children.length && children.every(child => child.state === 'complete')) parent.state = 'complete';
      }
      dispatch(item);
      refresh(item);
    }, 2600);
    timers.set(key, timer);
  }

  function dispatch(item) {
    for (const task of ready(item)) {
      task.state = 'running';
      simulateCompletion(item, task);
    }
  }

  function refresh(item) {
    const previousFocus = document.activeElement;
    renderProjects();
    if (item !== project) return;
    renderHeader();
    renderGraph();
    if (autoFit) fit();
    const activeSearch = previousFocus.classList.contains('asset-search');
    if (activeSearch && !outputTask) renderLibraryItems(list.scrollTop);
    else renderAssets();
    if (previousFocus.dataset.task) focusNode(previousFocus.dataset.task);
  }

  element('toggle-project').addEventListener('click', () => {
    project.paused = !project.paused;
    for (const task of project.tasks.filter(task => task.state === 'running')) simulateCompletion(project, task);
    dispatch(project);
    refresh(project);
  });

  element('new-project').addEventListener('click', () => {
    element('create-dialog').showModal();
    element('idea').focus();
  });
  document.querySelector('[data-close="create-dialog"]').addEventListener('click', () => element('create-dialog').close());
  element('idea').addEventListener('input', () => element('idea').setCustomValidity(''));
  element('create-form').addEventListener('submit', event => {
    event.preventDefault();
    const input = element('idea');
    const idea = input.value.trim();
    if (!idea) {
      input.setCustomValidity('请输入短剧 Idea');
      input.reportValidity();
      return;
    }
    const name = idea.split(/[。！？，、,.!?\n]/u)[0].trim() || idea;
    const item = makeProject(projects.length + 1, [...name].slice(0, 18).join(''), 'new');
    item.idea = idea;
    projects.unshift(item);
    input.value = '';
    element('create-dialog').close();
    dispatch(item);
    selectProject(item);
  });

  element('find-running').addEventListener('click', () => locate('running'));
  element('find-timeout').addEventListener('click', () => locate('timeout'));
  element('fit').addEventListener('click', fit);
  element('zoom-out').addEventListener('click', () => zoomAt(1 / 1.25, viewport.clientWidth / 2, viewport.clientHeight / 2));
  element('zoom-in').addEventListener('click', () => zoomAt(1.25, viewport.clientWidth / 2, viewport.clientHeight / 2));
  viewport.addEventListener('wheel', event => {
    if (event.target.closest('.view-tools, .task-detail')) return;
    event.preventDefault();
    const bounds = viewport.getBoundingClientRect();
    zoomAt(event.deltaY < 0 ? 1.13 : 1 / 1.13, event.clientX - bounds.left, event.clientY - bounds.top);
  }, { passive: false });
  viewport.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.target.closest('button, .task-detail')) return;
    drag = { id: event.pointerId, startX: event.clientX - view.x, startY: event.clientY - view.y };
    autoFit = false;
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add('dragging');
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    view.x = event.clientX - drag.startX;
    view.y = event.clientY - drag.startY;
    updateView();
  });
  viewport.addEventListener('lostpointercapture', () => {
    drag = undefined;
    viewport.classList.remove('dragging');
  });
  new ResizeObserver(() => {
    const next = { width: viewport.clientWidth, height: viewport.clientHeight };
    const previous = viewportSize;
    if (previous && previous.width === next.width && previous.height === next.height) return;
    viewportSize = next;
    if (autoFit) {
      fit();
    } else if (previous) {
      view.x += (next.width - previous.width) / 2;
      view.y += (next.height - previous.height) / 2;
      updateView();
    }
  }).observe(viewport);
  selectProject(sample);
}
