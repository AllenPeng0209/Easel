import { projectScope, runWithProjectScope } from './projectScopeHarness.mjs';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const file = new URL('../../web/frontend/src/lib/workshop.ts', import.meta.url);
const source = existsSync(file) ? readFileSync(file, 'utf8') : '';
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const api = {};
const requests = [];
let response = { ok: true, status: 200, data: {} };
runWithProjectScope(outputText, {
  exports: api, URL, AbortController, require: () => projectScope,
  fetch: async (url, options) => {
    requests.push({ url, options });
    const current = await (typeof response === 'function' ? response(url, options) : response);
    return { ok: current.ok, status: current.status, json: async () => current.data };
  },
});
const available = name => assert.equal(typeof api[name], 'function', `${name} is not implemented`);

function workshopView() {
  const component = readFileSync(new URL('../../web/frontend/src/components/VideoWorkshopPage.tsx', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(component, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const slots = [];
  let cursor = 0;
  let dirty = true;
  let effects = [];
  let tree;
  const frameHistory = [];
  const timers = new Map();
  const same = (a, b) => a && b && a.length === b.length && a.every((item, index) => Object.is(item, b[index]));
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, next => {
        const value = typeof next === 'function' ? next(slots[index].value) : next;
        if (!Object.is(value, slots[index].value)) { slots[index].value = value; dirty = true; }
      }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { current: initial };
      return slots[index];
    },
    useCallback(callback, deps) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].deps, deps)) slots[index] = { value: callback, deps };
      return slots[index].value;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].deps, deps)) {
        const previous = slots[index];
        slots[index] = { deps };
        effects.push(() => { previous?.cleanup?.(); slots[index].cleanup = effect(); });
      }
    },
  };
  const module = {};
  const jsx = (type, props, key) => ({ type, props, key });
  runWithProjectScope(compiled, {
    exports: module, URL, AbortController,
    window: { setInterval(callback) { const id = timers.size + 1; timers.set(id, callback); return id; },
      clearInterval(id) { timers.delete(id); }, addEventListener() {}, removeEventListener() {} },
    document: { visibilityState: 'visible', getElementById: () => null },
    require: name => name === 'react' ? hooks : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx, Fragment: 'fragment' }
      : name.endsWith('/workshop') ? api : name.endsWith('/ProjectSpace') ? { useIpPermissions: () => ({ role: 'owner', canOperate: true, canManageAccounts: true, isPlatformAdmin: true }) }
      : name.endsWith('/icons') ? new Proxy({}, { get: (_, name) => name }) : {},
  });
  const render = () => {
    cursor = 0; dirty = false; tree = module.default();
    frameHistory.push(findAll(node => node.type === 'iframe'));
  };
  const findAll = predicate => {
    const found = [];
    const visit = node => {
      if (!node || typeof node !== 'object') return;
      if (predicate(node)) found.push(node);
      const children = node.props?.children;
      (Array.isArray(children) ? children.flat(Infinity) : [children]).forEach(visit);
    };
    visit(tree); return found;
  };
  return {
    async flush() {
      for (let count = 0; count < 20; count++) {
        if (dirty) render();
        const pending = effects; effects = []; pending.forEach(effect => effect());
        await new Promise(resolve => setImmediate(resolve));
        if (!dirty && effects.length === 0) return;
      }
      throw new Error('Workshop view did not settle');
    },
    poll() { [...timers.values()].forEach(callback => callback()); },
    findAll,
    frameHistory,
    dispose() { slots.forEach(slot => slot?.cleanup?.()); timers.clear(); },
  };
}
const project = () => ({
  id: 'project-one', title: '竖版营销样片', brief: '六镜头', reference_url: '',
  aspect_ratio: '9:16', revision: 4, stage: 'editing', beeftv_canvas_id: 'canvas-one',
  engine_current_revision: 12, engine_dirty: false,
  deliverables: [
    { id: 'd1', path: '视频工坊/first.mp4', sha256: 'first', revision: 2, engine_revision: 7, created_at: '2026-11-01T12:00:00Z' },
    { id: 'd2', path: '视频工坊/latest.mp4', sha256: 'latest', revision: 4, engine_revision: 12, created_at: '2026-11-01T12:30:00Z' },
  ], reviews: [], comments: [],
});

const renderReceipt = (values = {}) => ({
  id: 'render-one', operation_id: 'operation-one', task_id: 'task-one',
  content_revision: 4, engine_revision: 12, status: 'queued', progress: 0,
  message: '', created_at: '2026-11-02T01:00:00Z', updated_at: '2026-11-02T01:00:00Z', ...values,
});

test('simultaneous export clicks share one real start request', async () => {
  available('WorkshopRenderFlow');
  response = { ok: true, status: 200, data: renderReceipt() };
  const flow = new api.WorkshopRenderFlow('project-one');
  const before = requests.length;
  const [first, second] = await Promise.all([flow.start(project()), flow.start(project())]);
  assert.equal(first.id, 'render-one');
  assert.equal(second.id, 'render-one');
  assert.equal(requests.length, before + 1);
  assert.equal(requests.at(-1).url, '/api/workshop/projects/project-one/renders');
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), { expected_revision: 4 });
});

test('failed export stops polling and preserves its actual failure message', async () => {
  available('WorkshopRenderFlow');
  response = (url, options) => ({ ok: true, status: 200,
    data: options.method === 'POST' ? renderReceipt() : renderReceipt({ status: 'failed', message: '时间线没有可见片段' }) });
  const flow = new api.WorkshopRenderFlow('project-one');
  await flow.start(project());
  const before = requests.length;
  assert.equal((await flow.poll()).status, 'failed');
  assert.equal((await flow.poll()).message, '时间线没有可见片段');
  assert.equal(requests.length, before + 1);
});

test('a reopened project resumes its saved export receipt without submitting another task', async () => {
  available('WorkshopRenderFlow');
  response = { ok: true, status: 200, data: renderReceipt({ status: 'running', progress: 0.4 }) };
  const flow = new api.WorkshopRenderFlow('project-one', [renderReceipt()]);
  const before = requests.length;
  assert.equal((await flow.poll()).progress, 0.4);
  await flow.start(project());
  assert.equal(requests.length, before + 1);
  assert.equal(requests.at(-1).url, '/api/workshop/projects/project-one/renders/render-one');
  assert.notEqual(requests.at(-1).options.method, 'POST');
});

test('successful export is collected once with the original content revision and its recorded artifact', async () => {
  available('WorkshopRenderFlow');
  const collected = renderReceipt({ status: 'succeeded', progress: 1, artifact_id: 'artifact-new', collected_revision: 5 });
  const delivered = { ...project(), revision: 5, renders: [collected] };
  response = { ok: true, status: 200, data: delivered };
  const flow = new api.WorkshopRenderFlow('project-one', [renderReceipt({ status: 'succeeded', progress: 1 })]);
  const before = requests.length;
  const [first, second] = await Promise.all([flow.collect(project()), flow.collect(project())]);
  assert.equal(first.revision, 5);
  assert.equal(second.revision, 5);
  assert.equal(flow.receipt.artifact_id, 'artifact-new');
  await flow.collect(project());
  assert.equal(requests.length, before + 1);
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), { expected_revision: 4 });
});

test('a collect conflict never rebases or resubmits the export behind the user', async () => {
  available('WorkshopRenderFlow');
  response = { ok: false, status: 409, data: { detail: '工程已修改，请重新导出' } };
  const flow = new api.WorkshopRenderFlow('project-one', [renderReceipt({ status: 'succeeded', progress: 1 })]);
  const before = requests.length;
  await assert.rejects(flow.collect(project()), /工程已修改，请重新导出/);
  await flow.poll();
  assert.equal(requests.length, before + 1);
  assert.equal(flow.receipt.artifact_id, undefined);
});

test('stale project-list receipts do not move a running export back to queued', () => {
  available('WorkshopRenderFlow');
  const flow = new api.WorkshopRenderFlow('project-one', [renderReceipt({ status: 'running', progress: 0.6 })]);
  flow.restore([renderReceipt()]);
  assert.equal(flow.receipt.status, 'running');
  assert.equal(flow.receipt.progress, 0.6);
});

test('an explicit retry of an unconfirmed submission keeps its recovered task over stale failed snapshots', async () => {
  available('WorkshopRenderFlow');
  const unknown = renderReceipt({ status: 'failed', submission_state: 'unconfirmed', task_id: '',
    updated_at: '2026-11-02T01:00:00.123100+00:00' });
  const recovered = renderReceipt({ status: 'queued', submission_state: 'confirmed',
    updated_at: '2026-11-02T01:00:00.123200+00:00' });
  const flow = new api.WorkshopRenderFlow('project-one', [unknown]);
  response = { ok: true, status: 200, data: recovered };
  const before = requests.length;
  const receipt = await flow.start(project());
  assert.equal(receipt.id, unknown.id);
  assert.equal(receipt.operation_id, unknown.operation_id);
  assert.equal(requests.length, before + 1);
  flow.restore([unknown]);
  assert.equal(flow.receipt.status, 'queued');
  assert.equal(flow.receipt.submission_state, 'confirmed');
  assert.equal(flow.receipt.task_id, 'task-one');
});

test('a newer saved reconciliation can restore a failed unconfirmed receipt to queued', () => {
  available('WorkshopRenderFlow');
  const unknown = renderReceipt({ status: 'failed', submission_state: 'unconfirmed', task_id: '',
    updated_at: '2026-11-02T01:00:00.123100+00:00' });
  const flow = new api.WorkshopRenderFlow('project-one', [unknown]);
  flow.restore([renderReceipt({ status: 'queued', submission_state: 'confirmed',
    updated_at: '2026-11-02T01:00:00.123200+00:00' })]);
  assert.equal(flow.receipt.status, 'queued');
  assert.equal(flow.receipt.submission_state, 'confirmed');
});

test('confirmed submission state wins an equal-timestamp stale unconfirmed failure', () => {
  available('WorkshopRenderFlow');
  const confirmed = renderReceipt({ status: 'queued', submission_state: 'confirmed' });
  const flow = new api.WorkshopRenderFlow('project-one', [confirmed]);
  flow.restore([renderReceipt({ status: 'failed', submission_state: 'unconfirmed', task_id: '' })]);
  assert.equal(flow.receipt.status, 'queued');
  assert.equal(flow.receipt.submission_state, 'confirmed');
});

test('a late status response cannot undo a newer explicit reconciliation of the same receipt', async () => {
  available('WorkshopRenderFlow');
  const unknown = renderReceipt({ status: 'failed', submission_state: 'unconfirmed', task_id: '',
    updated_at: '2026-11-02T01:00:00.100000+00:00' });
  const recovered = renderReceipt({ status: 'queued', submission_state: 'confirmed',
    updated_at: '2026-11-02T01:00:00.200000+00:00' });
  let resolveStatus;
  response = (_url, options) => options.method === 'POST'
    ? { ok: true, status: 200, data: recovered }
    : new Promise(resolve => { resolveStatus = resolve; });
  const flow = new api.WorkshopRenderFlow('project-one', [renderReceipt()]);
  const pendingStatus = flow.poll();
  flow.restore([unknown]);
  await flow.start(project());
  resolveStatus({ ok: true, status: 200, data: unknown });
  assert.equal((await pendingStatus).status, 'queued');
  assert.equal(flow.receipt.submission_state, 'confirmed');
});

test('review state does not confuse simulated approval with human approval', () => {
  available('reviewSummary');
  const p = project();
  p.reviews.push({ id: 'r1', revision: 4, artifact_sha256: 'latest', decision: 'approved', kind: 'simulated' });
  const result = api.reviewSummary(p, p.deliverables[1]);
  assert.equal(result.humanApproved, false);
  assert.equal(result.kind, 'simulated');
  assert.match(result.label, /模拟/);
});

test('approval of an old revision or different file never approves the current deliverable', () => {
  available('reviewSummary');
  const p = project();
  p.reviews = [
    { id: 'r1', revision: 2, artifact_sha256: 'latest', decision: 'approved', kind: 'human' },
    { id: 'r2', revision: 4, artifact_sha256: 'first', decision: 'approved', kind: 'human' },
  ];
  assert.equal(api.reviewSummary(p, p.deliverables[1]).humanApproved, false);
});

test('the latest review decision for the exact file and current revision wins', () => {
  available('reviewSummary');
  const p = project();
  p.reviews = [
    { id: 'r1', revision: 4, artifact_sha256: 'latest', decision: 'approved', kind: 'human' },
    { id: 'r2', revision: 4, artifact_sha256: 'latest', decision: 'changes_requested', kind: 'human' },
  ];
  const result = api.reviewSummary(p, p.deliverables[1]);
  assert.equal(result.humanApproved, false);
  assert.equal(result.decision, 'changes_requested');
});

test('a matching human approval is recognized without mutating project history', () => {
  available('reviewSummary');
  const p = project();
  p.reviews.push({ id: 'r1', revision: 4, artifact_sha256: 'latest', decision: 'approved', kind: 'human' });
  assert.equal(api.reviewSummary(p, p.deliverables[1]).humanApproved, true);
  assert.equal(p.reviews.length, 1);
});

test('selecting a historical delivery never shows current approval even when bytes are reused', () => {
  available('reviewSummary');
  const p = project();
  p.reviews.push({ id: 'r1', revision: 4, artifact_sha256: 'first', decision: 'approved', kind: 'human' });
  assert.equal(api.reviewSummary(p, p.deliverables[0]).humanApproved, false);
});

test('project polling reads the selected enhanced project rather than trusting the stale list row', async () => {
  available('fetchWorkshopProjectsWithActive');
  const listed = project();
  const selected = { ...project(), id: 'selected' };
  const { engine_current_revision, engine_dirty, ...stale } = selected;
  response = url => ({ ok: true, status: 200,
    data: url === '/api/workshop/projects' ? { projects: [listed, stale] }
      : { ...selected, engine_current_revision: 13, engine_dirty: true } });
  const result = await api.fetchWorkshopProjectsWithActive('selected');
  assert.equal(result.projects.find(item => item.id === 'selected').engine_dirty, true);
  assert.equal(result.projects.find(item => item.id === 'selected').engine_current_revision, 13);
  assert.equal(result.projects[0].id, 'project-one');
  assert.equal(requests.at(-1).url, '/api/workshop/projects/selected');
});

test('media URLs preserve Unicode segments while refusing traversal and remote URLs', () => {
  available('workshopMediaUrl');
  assert.equal(api.workshopMediaUrl('视频工坊/成片 v2.mp4'), '/api/media/%E8%A7%86%E9%A2%91%E5%B7%A5%E5%9D%8A/%E6%88%90%E7%89%87%20v2.mp4');
  for (const path of ['../secret.mp4', '/etc/private.mp4', 'https://other.test/video.mp4', 'a\\private.mp4', 'a//b.mp4']) {
    assert.equal(api.workshopMediaUrl(path), null, path);
  }
});

test('timestamped feedback sends the selected project revision and explicit review kind', async () => {
  available('addWorkshopComment');
  response = { ok: true, status: 200, data: project() };
  await api.addWorkshopComment('project one', { expected_revision: 4, at_seconds: 8.5, notes: '字幕提前半秒', kind: 'simulated' });
  const last = requests.at(-1);
  assert.equal(last.url, '/api/workshop/projects/project%20one/comments');
  assert.equal(last.options.method, 'POST');
  assert.deepEqual(JSON.parse(last.options.body), { expected_revision: 4, at_seconds: 8.5, notes: '字幕提前半秒', kind: 'simulated' });
});

test('version conflicts remain visible and are not silently retried with a newer revision', async () => {
  available('updateWorkshopProject');
  response = { ok: false, status: 409, data: { detail: '项目已被其他窗口修改，请重新读取' } };
  const before = requests.length;
  await assert.rejects(api.updateWorkshopProject('p', { expected_revision: 4, title: 'new' }), /项目已被其他窗口修改/);
  assert.equal(requests.length, before + 1);
});

function enterProject(view, id = 'project-one') {
  const button = view.findAll(node => node.type === 'button' && node.props['data-project-id'] === id)[0];
  assert.ok(button, `Project ${id} needs an explicit entry action`);
  button.props.onClick();
}
