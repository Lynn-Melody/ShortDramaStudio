'use strict';

const states = { waiting: '等待', running: '运行', timeout: '超时', complete: '完成' };
const types = ['全部', '剧本', '角色与场景', '分镜', '音频', '视频'];
const columns = ['策划', '剧本与设定', '镜头制作', '音视频', '交付'];
const blueprint = [
  ['idea', '创意拆解', 0, 206, [], 'complete'],
  ['outline', '分集大纲', 1, 88, ['idea'], 'complete'],
  ['cast', '角色与场景设定', 1, 206, ['idea'], 'complete'],
  ['script', '分集剧本', 1, 324, ['outline'], 'complete'],
  ['board1', '第 1 集分镜', 2, 88, ['script', 'cast'], 'complete'],
  ['board2', '第 2 集分镜', 2, 206, ['script', 'cast'], 'running'],
  ['voice', '角色试音', 2, 324, ['cast'], 'complete'],
  ['video', '第 1 集镜头生成', 3, 88, ['board1'], 'running'],
  ['sound', '配乐生成', 3, 206, ['script'], 'timeout'],
  ['video2', '第 2 集镜头生成', 3, 324, ['board2'], 'waiting'],
  ['rough', '粗剪合成', 4, 146, ['video', 'video2', 'voice'], 'waiting'],
  ['deliver', '成片导出', 4, 267, ['rough', 'sound'], 'waiting'],
];


const compactSlots = [
  [0, 0], [0, 1], [0, 2], [0, 3],
  [1, 0], [1, 1], [1, 2], [1, 3],
  [2, 0], [2, 1], [2, 2], [2, 3],
];
const wideSize = { width: 890, height: 430, nodeWidth: 157, nodeHeight: 64, fontSize: 14 };
const compactSize = { width: 410, height: 451, nodeWidth: 122, nodeHeight: 60, fontSize: 13 };

function graphLayout(tasks, viewportWidth) {
  const compact = viewportWidth < 700;
  const size = compact ? compactSize : wideSize;
  const labels = compact ? ['策划与剧本', '素材生成', '视频与交付'] : columns;
  return {
    ...size,
    compact,
    labels: labels.map((name, column) => ({ name, x: compact ? 8 + column * 135 : 16 + column * 175 })),
    nodes: tasks.map((task, index) => {
      if (!compact) return task;
      const [column, row] = compactSlots[index];
      return { ...task, x: 8 + column * 135, y: 55 + row * 101 };
    }),
  };
}

function fitGraph(layout, width, height) {
  const zoom = Math.min(1.2, Math.max(.22, Math.min((width - 18) / layout.width, (height - 43) / layout.height)));
  return { zoom, x: (width - layout.width * zoom) / 2, y: (height - layout.height * zoom) / 2 - 10 };
}

function makeProject(id, name, mode = 'sample') {
  const tasks = blueprint.map(([id, name, column, y, deps, sample]) => ({
    id, name, x: 16 + column * 175, y, deps,
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
  return project.tasks.filter(task => task.state === 'waiting' &&
    task.deps.every(dep => project.tasks.some(candidate => candidate.id === dep && candidate.state === 'complete')));
}

const taskOutputs = (project, taskId) => project.assets.filter(asset => asset.task === taskId);
const libraryAssets = (project, filters) => project.assets.filter(asset =>
  (filters.type === types[0] || asset.type === filters.type) &&
  (!filters.episode || asset.episode === filters.episode) &&
  (!filters.shot || asset.shot === filters.shot));
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
  module.exports = { makeProject, progress, projectState, ready, taskOutputs, libraryAssets, versionGroups, graphLayout, fitGraph };
}

if (typeof document !== 'undefined') mount();

function mount() {
  const element = id => document.getElementById(id);
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
    asset('cast', 'lead', '林夏 · 角色设定', '角色与场景', 'common', '', 1, true),
    asset('cast', 'room', '电话亭 · 场景设定', '角色与场景', 'common', '', 1, true),
    asset('voice', 'lead-voice', '林夏 · 试音', '音频', 'common', '', 1, false),
    asset('script', 'script1', '第 1 集 · 失效号码', '剧本', '1', '', 1, true, '外景 · 雨夜。林夏经过电话亭，雨声里响起一段熟悉的铃声。'),
    asset('script', 'script2', '第 2 集 · 空白录音', '剧本', '2', '', 1, true, '内景 · 清晨。林夏反复切换录音轨道，终于听见了一句低语。'),
    asset('board1', 's01', '雨夜电话亭', '分镜', '1', '01', 1, true),
    asset('board1', 's01', '雨夜电话亭', '分镜', '1', '01', 2, false),
    asset('board1', 's02', '接起电话', '分镜', '1', '02', 1, true),
    asset('board1', 's03', '玻璃倒影', '分镜', '1', '03', 1, false),
  );
  const second = makeProject(2, '雾都回信', 'paused');
  second.tasks[0].state = 'complete';
  second.tasks[1].state = 'complete';
  second.assets.push(asset('outline', 'outline', '雾都回信 · 分集大纲', '剧本', 'common', '', 1, true, '每当雾起，一封寄往过去的信就会出现在门前。'));
  const third = makeProject(3, '第七次日落', 'complete');
  third.assets.push(asset('deliver', 'final', '第七次日落 · 成片', '视频', 'common', '', 1, true));
  const projects = [sample, second, third];
  const timers = new Map();
  let project = sample;
  let currentTask = null;
  let outputTask = null;
  let filters = { type: types[0], episode: '', shot: '' };
  let savedScroll = 0;
  let view = { x: 0, y: 0, zoom: 1 };
  let drag;
  let autoFit = true;
  let graph = graphLayout(project.tasks, 890);
  let viewportSize;
  let focusIndex = {};
  const viewport = element('viewport');
  const list = element('asset-list');
  const detail = element('task-detail');

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
      outputTask = null;
      filters = { type: types[0], episode: '', shot: '' };
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
    graph = graphLayout(project.tasks, viewport.clientWidth);
    element('world').classList.toggle('compact', graph.compact);
    text('task-count', project.tasks.length + ' 个节点');
    element('find-running').disabled = !project.tasks.some(task => task.state === 'running');
    element('find-timeout').disabled = !project.tasks.some(task => task.state === 'timeout');
    const active = project.tasks.find(task => task.id === currentTask);
    const related = new Set(active ? [
      ...active.deps, ...project.tasks.filter(task => task.deps.includes(active.id)).map(task => task.id),
    ] : []);
    const headings = graph.labels.map(({ name, x }) => {
      const label = create('span', 'graph-column', name);
      label.style.left = x + 'px';
      label.style.top = graph.compact ? '20px' : '39px';
      return label;
    });
    const nodes = graph.nodes.map(task => {
      const node = button('', 'node ' + task.state, () => setTask(task.id, true));
      node.dataset.task = task.id;
      node.setAttribute('aria-pressed', String(task.id === currentTask));
      if (active && task.id !== active.id) node.classList.add(related.has(task.id) ? 'related' : 'dim');
      node.style.left = task.x + 'px';
      node.style.top = task.y + 'px';
      node.append(create('span', 'node-name', task.name), create('span', 'node-state', states[task.state]));
      return node;
    });
    element('nodes').replaceChildren(...headings, ...nodes);
    const paths = graph.nodes.flatMap(task => task.deps.map(id => {
      const dep = graph.nodes.find(candidate => candidate.id === id);
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      const mid = graph.nodeHeight / 2;
      let coordinates;
      if (task.x === dep.x) {
        const down = task.y > dep.y;
        const start = dep.y + (down ? graph.nodeHeight : 0);
        const end = task.y + (down ? 0 : graph.nodeHeight);
        const x = task.x + graph.nodeWidth / 2;
        coordinates = 'M ' + x + ' ' + start + ' L ' + x + ' ' + end;
        if (Math.abs(task.y - dep.y) > 110) {
          const lane = task.x + graph.nodeWidth + 5;
          coordinates = 'M ' + (task.x + graph.nodeWidth) + ' ' + (dep.y + mid) +
            ' H ' + lane + ' V ' + (task.y + mid) + ' H ' + (task.x + graph.nodeWidth);
        }
      } else {
        const x1 = dep.x + graph.nodeWidth, x2 = task.x, y1 = dep.y + mid, y2 = task.y + mid;
        const gap = Math.min(66, (x2 - x1) / 2);
        coordinates = 'M ' + x1 + ' ' + y1 + ' C ' + (x1 + gap) + ' ' + y1 +
          ', ' + (x2 - gap) + ' ' + y2 + ', ' + x2 + ' ' + y2;
      }
      path.setAttribute('d', coordinates);
      const direct = active && (active.id === id || active.id === task.id);
      path.setAttribute('class', 'edge' + (direct ? ' related' : active ? ' dim' : ''));
      path.setAttribute('marker-end', direct ? 'url(#arrow-active)' : 'url(#arrow)');
      return path;
    }));
    element('connections').replaceChildren(...paths);
    renderDetail(active);
  }

  function renderDetail(task) {
    detail.hidden = !task;
    if (!task) return;
    const heading = create('div', 'detail-heading');
    const close = button('×', '', () => {
      setTask(null);
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

  function setTask(id, focus) {
    currentTask = id;
    renderGraph();
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
    const matching = graph.nodes.filter(task => task.state === status);
    if (!matching.length) return;
    focusIndex[status] = ((focusIndex[status] ?? -1) + 1) % matching.length;
    const task = matching[focusIndex[status]];
    autoFit = false;
    view.zoom = Math.max(view.zoom, .87);
    view.x = viewport.clientWidth / 2 - (task.x + graph.nodeWidth / 2) * view.zoom;
    view.y = viewport.clientHeight / 2 - (task.y + graph.nodeHeight / 2) * view.zoom;
    setTask(task.id, true);
    updateView();
  }

  function renderAssets(scroll = list.scrollTop) {
    const controls = element('asset-controls');
    let visible;
    let items;
    if (outputTask) {
      visible = latestFirst(taskOutputs(project, outputTask));
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
      visible = libraryAssets(project, filters);
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
      const episodeChoices = [...new Set(project.assets.map(item => item.episode).filter(value => value !== 'common'))].sort();
      const shotChoices = [...new Set(project.assets.filter(item => !filters.episode || item.episode === filters.episode)
        .map(item => item.shot).filter(Boolean))].sort();
      selectors.append(
        selectFilter('集数', 'episode', [['', '所有集数'], ['common', '全剧共用'], ...episodeChoices.map(value => [value, episodeName(value)])]),
        selectFilter('镜头', 'shot', [['', '所有镜头'], ...shotChoices.map(value => [value, '镜头 ' + value])]),
      );
      controls.replaceChildren(tabList, selectors);
      items = versionGroups(visible).map(assetCard);
      text('asset-count', items.length + ' 项素材');
    }
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
      if (task.id === 'video' || task.id === 'video2') {
        const episode = task.id === 'video' ? '1' : '2';
        item.assets.push(asset(task.id, task.id, episodeName(episode) + ' · 镜头片段', '视频', episode, '01', 1, false));
      }
      if (task.id === 'outline' && item.idea) {
        item.assets.push(asset(task.id, 'outline', '分集大纲', '剧本', 'common', '', 1, false, item.idea));
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
    renderAssets();
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
    const switchLayout = graph.compact !== (next.width < 700);
    if (switchLayout) renderGraph();
    if (autoFit || switchLayout) {
      fit();
    } else if (previous) {
      view.x += (next.width - previous.width) / 2;
      view.y += (next.height - previous.height) / 2;
      updateView();
    }
  }).observe(viewport);
  selectProject(sample);
}
