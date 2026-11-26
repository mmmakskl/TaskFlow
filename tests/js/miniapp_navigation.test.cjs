const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../app/web/static/activity.js'), 'utf8') + '\n' + fs.readFileSync(path.join(__dirname, '../../app/web/static/miniapp.js'), 'utf8');

function element() {
  const classes = new Set();
  return {
    innerHTML: '', textContent: '', dataset: {},
    classList: {
      add: name => classes.add(name),
      remove: name => classes.delete(name),
      contains: name => classes.has(name),
    },
  };
}

function harness() {
  const nodes = Object.fromEntries(
    ['view', 'nav', 'modal', 'modalWrap', 'toast', 'themeToggle', 'profileName', 'confirmAction']
      .map(id => [id, element()])
  );
  const task = {
    id: 12, parent_task_id: 11, parent: { id: 11, title: 'Эпик', status: 'TODO' },
    title: 'Подзадача', status: 'TODO', version: 1, kind: 'PERSONAL',
    can_manage: true, can_rerate: false, is_evaluator: false, blocked_by_parent: false,
    participants: ['Макс'], proposal_by: 'Макс', points: 200, awarded_points: null,
    deadline: null, estimated_minutes: null, comment: null, template_snapshot: null,
    children: [], subtask_summary: { count: 0, done_count: 0, agreed_points: 0, progress: 0 },
    blockers: [], links: [],
  };
  const epic = {
    ...task, id: 11, parent_task_id: null, parent: null, title: 'Эпик',
    children: [{ id: 12, title: 'Подзадача', status: 'TODO', points: 200, version: 1 }],
  };
  const requests = [];
  let failAction = false;
  const context = vm.createContext({
    window: { Telegram: { WebApp: {
      initData: '', BackButton: { show() {}, hide() {}, onClick() {} },
      ready() {}, expand() {}, onEvent() {},
    } } },
    document: {
      getElementById: id => nodes[id],
      documentElement: { dataset: {} },
      querySelector: () => ({ content: '' }),
    },
    localStorage: { getItem: () => null, setItem() {} },
    crypto: { randomUUID: () => 'operation-key' },
    setTimeout: () => {},
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      if (url === '/api/miniapp/tasks/12/actions') {
        if (failAction) return { ok: false, status: 409, json: async () => ({ detail: 'Конфликт' }) };
        task.status = JSON.parse(options.body).status;
        task.version += 1;
      }
      if (url === '/api/miniapp/tasks/12') return { ok: true, status: 200, json: async () => ({ ...task }) };
      if (url === '/api/miniapp/tasks/11') return { ok: true, status: 200, json: async () => ({
        ...epic,
        children: [{ ...epic.children[0], status: task.status, version: task.version }],
        subtask_summary: {
          count: 1, done_count: Number(task.status === 'DONE'), agreed_points: 200,
          progress: task.status === 'DONE' ? 100 : 0,
        },
      }) };
      if (url === '/api/miniapp/tags') return { ok: true, status: 200, json: async () => [] };
      if (url === '/api/miniapp/bootstrap') return { ok: true, status: 200, json: async () => ({
        user: { id: 1, name: 'Макс', timezone: 'Europe/Moscow' },
        week: { label: 'Эта неделя', scores: [
          { user_id: 1, name: 'Макс', net: 0, target: 100, earned: 0, lost: 0 },
          { user_id: 2, name: 'Вика', net: 0, target: 100, earned: 0, lost: 0 },
        ] },
        active_preview: [task], active_count: 1, pending_count: 0,
      }) };
      if (url.startsWith('/api/miniapp/tasks?')) {
        return { ok: true, status: 200, json: async () => ({
          items: [], count: 0, page: 2, summary: null,
        }) };
      }
      throw new Error(`Unexpected request: ${url}`);
    },
    URLSearchParams,
    Intl,
  });
  vm.runInContext(source, context);
  vm.runInContext("window.Telegram.WebApp.initData = 'valid'; state.page = 'tasks'; state.scope = 'mine'; state.filter = 'active'; state.pageIndex = 2", context);
  return { context, nodes, task, requests, setFailAction: value => { failAction = value; } };
}

test('status change keeps the subtask card open and preserves the task list location', async () => {
  const { context, nodes, requests } = harness();
  await vm.runInContext('render()', context);
  await vm.runInContext('taskDetail(12)', context);
  assert.match(nodes.modal.innerHTML, /Подзадача/);
  assert.match(nodes.modal.innerHTML, /onclick="taskAction\(12,1,'IN_PROGRESS'\)"/);

  await vm.runInContext("taskAction(12, 1, 'IN_PROGRESS')", context);

  assert.equal(nodes.modalWrap.classList.contains('open'), true);
  assert.match(nodes.modal.innerHTML, /В работе/);
  assert.match(nodes.modal.innerHTML, /К выполнению/);
  assert.equal(vm.runInContext('state.pageIndex', context), 2);
  assert.equal(vm.runInContext('state.filter', context), 'active');
  assert.equal(requests.at(-1).url, '/api/miniapp/tasks/12');

  await vm.runInContext("taskAction(12, 2, 'TODO')", context);
  assert.equal(nodes.modalWrap.classList.contains('open'), true);
  assert.match(nodes.modal.innerHTML, /К выполнению/);
  assert.match(nodes.modal.innerHTML, /onclick="taskAction\(12,3,'IN_PROGRESS'\)"/);
  vm.runInContext('closeModal()', context);
  assert.match(nodes.view.innerHTML, /Задачи/);
  assert.equal(vm.runInContext('state.pageIndex', context), 2);
});

test('status change from the overview keeps the subtask open above the overview', async () => {
  const { context, nodes } = harness();
  vm.runInContext("state.page = 'home'", context);
  await vm.runInContext('render()', context);
  await vm.runInContext('taskDetail(12)', context);
  await vm.runInContext("taskAction(12, 1, 'IN_PROGRESS')", context);

  assert.equal(nodes.modalWrap.classList.contains('open'), true);
  assert.match(nodes.modal.innerHTML, /В работе/);
  assert.match(nodes.view.innerHTML, /Мой фокус/);
  assert.equal(vm.runInContext('state.page', context), 'home');
});

test('confirmed completion returns to the updated subtask card', async () => {
  const { context, nodes, requests } = harness();
  await vm.runInContext('taskDetail(12)', context);
  vm.runInContext("confirmTaskAction(12, 1, 'DONE')", context);
  await nodes.confirmAction.onclick();

  assert.equal(nodes.modalWrap.classList.contains('open'), true);
  assert.match(nodes.modal.innerHTML, /Выполнено/);
  assert.equal(requests.at(-1).url, '/api/miniapp/tasks/12');
  await vm.runInContext('taskDetail(11)', context);
  assert.match(nodes.modal.innerHTML, /Подзадачи · 1\/1/);
});

test('moving a completed subtask back to work keeps its card open', async () => {
  const { context, nodes, task } = harness();
  task.status = 'DONE';
  await vm.runInContext('taskDetail(12)', context);
  vm.runInContext("confirmTaskAction(12, 1, 'IN_PROGRESS')", context);
  await nodes.confirmAction.onclick();

  assert.equal(nodes.modalWrap.classList.contains('open'), true);
  assert.match(nodes.modal.innerHTML, /В работе/);
  assert.match(nodes.modal.innerHTML, /onclick="taskAction\(12,2,'TODO'\)"/);
});

test('failed status change keeps the subtask available with its previous status', async () => {
  const { context, nodes, setFailAction } = harness();
  await vm.runInContext('taskDetail(12)', context);
  setFailAction(true);
  vm.runInContext("confirmTaskAction(12, 1, 'DONE')", context);
  await nodes.confirmAction.onclick();

  assert.equal(nodes.modalWrap.classList.contains('open'), true);
  assert.match(nodes.modal.innerHTML, /К выполнению/);
  assert.match(nodes.toast.textContent, /Конфликт/);
});

test('calendar covers leap year and escapes tracker names', () => {
  const { context } = harness();
  const html = vm.runInContext(`activityState.year=2024;activityState.today='2024-02-29';trackerCard({id:2,name:'<script>',metric:'TASKS',color:'#123456',days:{'2024-02-29':4},active_days:1,current_streak:1,best_streak:1})`, context);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /2024-02-29: 4 задач/);
  assert.equal((html.match(/class="activity-day level-/g) || []).length, 366);
  assert.match(html, /day-today/);
  assert.match(html, /day-future/);
});

test('activity uses fixed metric thresholds', () => {
  const { context } = harness();
  assert.deepEqual(Array.from(vm.runInContext("[0,1,29,30,59,60,119,120].map(n=>activityLevel('TIME',n))",context)), [0,1,1,2,2,3,3,4]);
  assert.deepEqual(Array.from(vm.runInContext("[0,1,2,3,4,9].map(n=>activityLevel('TASKS',n))",context)), [0,1,2,3,4,4]);
});

test('template volume handler is valid inside its HTML attribute', () => {
  const { context } = harness();
  const html = vm.runInContext("templateUsageField({pricing_mode:'PER_TIME',points:100,base_quantity:30}).field", context);
  assert.match(html, /oninput="updateTemplateReward\(100,30,'PER_TIME'\)"/);
});

test('a late task response cannot reopen a modal after navigation', async () => {
  const { context, nodes } = harness();
  let resolveRequest;
  context.fetch = () => new Promise(resolve => { resolveRequest = resolve; });
  const pending = vm.runInContext('taskDetail(12)', context);
  vm.runInContext("go('more')", context);
  resolveRequest({ ok: true, status: 200, json: async () => ({}) });
  await pending;
  assert.equal(nodes.modalWrap.classList.contains('open'), false);
});
