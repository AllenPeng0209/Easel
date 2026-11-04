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

test('an old content or canvas revision cannot collect into the current project', async () => {
  available('WorkshopRenderFlow');
  const flow = new api.WorkshopRenderFlow('project-one', [renderReceipt({ status: 'succeeded', progress: 1 })]);
  const before = requests.length;
  await assert.rejects(flow.collect({ ...project(), revision: 5 }), /重新导出/);
  await assert.rejects(flow.collect({ ...project(), engine_current_revision: 13 }), /重新导出/);
  assert.equal(requests.length, before);
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

test('editing the upstream canvas invalidates approval and disables review without an Easel revision change', () => {
  available('deliveryReviewState');
  const p = project();
  p.reviews.push({ id: 'r1', revision: 4, artifact_sha256: 'latest', decision: 'approved', kind: 'human' });
  p.engine_current_revision = 13;
  p.engine_dirty = true;
  const summary = api.reviewSummary(p, p.deliverables[1]);
  assert.equal(summary.humanApproved, false);
  assert.match(summary.label, /重新导出/);
  assert.equal(api.deliveryReviewState(p, p.deliverables[1]).canReview, false);
  assert.equal(p.revision, 4);
});

test('a canvas revision mismatch is blocked even if a stale dirty flag says false', () => {
  available('deliveryReviewState');
  const p = project();
  p.engine_current_revision = 13;
  assert.equal(api.deliveryReviewState(p, p.deliverables[1]).canReview, false);
  assert.match(api.deliveryReviewState(p, p.deliverables[1]).label, /重新导出/);
});

test('unconfirmed canvas state never retains old human approval or enables its review controls', () => {
  available('deliveryReviewState');
  const p = project();
  p.reviews.push({ id: 'r1', revision: 4, artifact_sha256: 'latest', decision: 'approved', kind: 'human' });
  p.engine_current_revision = null;
  p.engine_dirty = true;
  p.engine_message = '无法确认画布当前版本';
  assert.equal(api.reviewSummary(p, p.deliverables[1]).humanApproved, false);
  assert.equal(api.deliveryReviewState(p, p.deliverables[1]).canReview, false);
  assert.match(api.deliveryReviewState(p, p.deliverables[1]).message, /无法确认/);
});

test('a newly exported delivery that matches the upstream canvas becomes reviewable', () => {
  available('deliveryReviewState');
  const p = project();
  p.engine_current_revision = 13;
  p.deliverables[1].engine_revision = 13;
  assert.equal(api.deliveryReviewState(p, p.deliverables[1]).canReview, true);
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

test('failed enhanced-project polling marks its canvas state unconfirmed instead of showing stale approval', async () => {
  available('fetchWorkshopProjectsWithActive');
  const listed = project();
  response = url => url === '/api/workshop/projects'
    ? { ok: true, status: 200, data: { projects: [listed] } }
    : { ok: false, status: 503, data: { detail: '画布版本暂时不可查询' } };
  const result = await api.fetchWorkshopProjectsWithActive('project-one');
  assert.equal(result.projects[0].engine_dirty, true);
  assert.equal(result.projects[0].engine_current_revision, null);
  assert.match(result.projects[0].engine_message, /画布版本暂时不可查询/);
});

test('media URLs preserve Unicode segments while refusing traversal and remote URLs', () => {
  available('workshopMediaUrl');
  assert.equal(api.workshopMediaUrl('视频工坊/成片 v2.mp4'), '/api/media/%E8%A7%86%E9%A2%91%E5%B7%A5%E5%9D%8A/%E6%88%90%E7%89%87%20v2.mp4');
  for (const path of ['../secret.mp4', '/etc/private.mp4', 'https://other.test/video.mp4', 'a\\private.mp4', 'a//b.mp4']) {
    assert.equal(api.workshopMediaUrl(path), null, path);
  }
});

test('only a local HTTP runtime can be embedded in the workshop', () => {
  available('safeRuntimeUrl');
  assert.equal(api.safeRuntimeUrl('http://127.0.0.1:8843/'), 'http://127.0.0.1:8843/');
  for (const url of ['javascript:alert(1)', 'https://other.test/', 'http://user:secret@localhost:8843/', 'file:///tmp/app.html']) {
    assert.equal(api.safeRuntimeUrl(url), null, url);
  }
});

test('a selected project opens its linked canvas without leaking credentials or taking paths from the id', () => {
  available('workshopCanvasUrl');
  assert.equal(api.workshopCanvasUrl('http://127.0.0.1:8843/', 'canvas-one'), 'http://127.0.0.1:8843/canvas/canvas-one');
  assert.equal(api.workshopCanvasUrl('http://127.0.0.1:8843/', ''), 'http://127.0.0.1:8843/');
  assert.equal(api.workshopCanvasUrl('http://other.test/', 'canvas-one'), null);
  assert.equal(api.workshopCanvasUrl('http://127.0.0.1:8843/', '../settings'), null);
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

test('connecting a project canvas sends its expected revision through the dedicated local route', async () => {
  available('connectWorkshopCanvas');
  response = { ok: true, status: 200, data: project() };
  const result = await api.connectWorkshopCanvas('project one', 4);
  assert.equal(result.beeftv_canvas_id, 'canvas-one');
  const last = requests.at(-1);
  assert.equal(last.url, '/api/workshop/projects/project%20one/canvas');
  assert.equal(last.options.method, 'POST');
  assert.deepEqual(JSON.parse(last.options.body), { expected_revision: 4 });
});

function enterProject(view, id = 'project-one') {
  const button = view.findAll(node => node.type === 'button' && node.props['data-project-id'] === id)[0];
  assert.ok(button, `Project ${id} needs an explicit entry action`);
  button.props.onClick();
}

test('workshop opens a video list without selecting or mounting a production project', async () => {
  const draft = { ...project(), id: 'draft', title: '新品上新', stage: 'draft', deliverables: [] };
  response = url => url === '/api/workshop/status'
    ? { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } }
    : { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: [project(), draft] } : project() };
  const view = workshopView();
  try {
    await view.flush();
    assert.equal(view.findAll(node => node.props?.['aria-label'] === '视频项目列表').length, 1);
    assert.equal(view.findAll(node => node.props?.['data-project-id']).length, 2);
    assert.equal(view.findAll(node => node.props?.id === 'workshop-review-panel').length, 0);
    assert.equal(view.findAll(node => node.props?.id === 'workshop-project').length, 0);
    assert.ok(view.frameHistory.every(frames => frames.length === 0));
  } finally { view.dispose(); }
});

test('search and status filters select video rows, then back returns to the filtered list', async () => {
  const draft = { ...project(), id: 'draft', title: '新品上新', brief: '咖啡门店', stage: 'draft', deliverables: [] };
  response = url => url === '/api/workshop/status'
    ? { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } }
    : { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: [project(), draft] }
      : url.endsWith('/draft') ? draft : project() };
  const view = workshopView();
  try {
    await view.flush();
    const search = () => view.findAll(node => node.props?.['aria-label'] === '搜索视频主题或创作要求')[0];
    assert.ok(search());
    search().props.onChange({ target: { value: '咖啡' } }); await view.flush();
    assert.deepEqual(view.findAll(node => node.props?.['data-project-id']).map(node => node.props['data-project-id']), ['draft']);
    enterProject(view, 'draft'); await view.flush();
    assert.equal(view.findAll(node => node.type === 'iframe')[0]?.props.src, 'http://127.0.0.1:8843/canvas/canvas-one');
    view.findAll(node => node.type === 'button' && node.props.children === '返回视频列表')[0].props.onClick(); await view.flush();
    assert.equal(search().props.value, '咖啡');
    assert.equal(view.findAll(node => node.type === 'iframe').length, 0);
    search().props.onChange({ target: { value: '' } });
    view.findAll(node => node.props?.['aria-label'] === '筛选：草稿')[0].props.onClick();
    await view.flush();
    assert.deepEqual(view.findAll(node => node.props?.['data-project-id']).map(node => node.props['data-project-id']), ['draft']);
  } finally { view.dispose(); }
});

test('five compact status filters hide empty counts and keep archived videos in a separate view', async () => {
  const draft = { ...project(), id: 'draft', title: '新品上新', stage: 'draft', deliverables: [] };
  const archived = { ...draft, id: 'archived', title: '历史主题', archived_at: '2026-11-02T03:00:00Z' };
  response = url => url === '/api/workshop/status'
    ? { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } }
    : { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: [draft, archived] } : draft };
  const view = workshopView();
  const text = node => typeof node === 'string' || typeof node === 'number' ? String(node)
    : Array.isArray(node) ? node.map(text).join('') : node?.props ? text(node.props.children) : '';
  try {
    await view.flush();
    const filters = view.findAll(node => node.type === 'button' && node.props['aria-label']?.startsWith('筛选：'));
    assert.deepEqual(filters.map(node => node.props['aria-label']), ['筛选：全部', '筛选：草稿', '筛选：制作中', '筛选：待审核', '筛选：待发布', '筛选：已发布']);
    assert.equal(text(filters.find(node => node.props['aria-label'] === '筛选：制作中')), '制作中');
    assert.equal(view.findAll(node => node.props?.['aria-label'] === '筛选视频状态').length, 0);
    assert.deepEqual(view.findAll(node => node.props?.['data-project-id']).map(node => node.props['data-project-id']), ['draft']);
    view.findAll(node => node.props?.['aria-label'] === '查看归档视频')[0].props.onClick(); await view.flush();
    assert.deepEqual(view.findAll(node => node.props?.['data-project-id']).map(node => node.props['data-project-id']), ['archived']);
    view.findAll(node => node.props?.['aria-label'] === '查看归档视频')[0].props.onClick(); await view.flush();
    assert.deepEqual(view.findAll(node => node.props?.['data-project-id']).map(node => node.props['data-project-id']), ['draft']);
  } finally { view.dispose(); }
});

test('new project is a modal over the list and cancellation does not enter an existing project', async () => {
  response = url => url === '/api/workshop/status'
    ? { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } }
    : { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: [project()] } : project() };
  const view = workshopView();
  try {
    await view.flush();
    view.findAll(node => node.type === 'button' && node.props.children === '+ 新建视频项目')[0].props.onClick();
    await view.flush();
    assert.equal(view.findAll(node => node.props?.role === 'dialog' && node.props['aria-modal']).length, 1);
    const form = view.findAll(node => typeof node.type === 'function' && typeof node.props?.onSave === 'function')[0];
    assert.ok(form); form.props.onCancel(); await view.flush();
    assert.equal(view.findAll(node => node.props?.role === 'dialog').length, 0);
    assert.equal(view.findAll(node => node.props?.id === 'workshop-project').length, 0);
    assert.equal(view.findAll(node => node.props?.['aria-label'] === '视频项目列表').length, 1);
  } finally { view.dispose(); }
});

test('creating a project from the list enters only the newly saved production project', async () => {
  let list = [project()];
  const draft = { ...project(), id: 'new-project', title: '新品咖啡', deliverables: [], stage: 'editing', beeftv_canvas_id: 'new-canvas' };
  response = (url, options) => {
    if (url === '/api/workshop/status') return { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } };
    if (url === '/api/workshop/projects' && options.method === 'POST') { list = [draft, ...list]; return { ok: true, status: 201, data: draft }; }
    return { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: list } : draft };
  };
  const view = workshopView();
  try {
    await view.flush();
    view.findAll(node => node.type === 'button' && node.props.children === '+ 新建视频项目')[0].props.onClick(); await view.flush();
    const form = view.findAll(node => typeof node.type === 'function' && typeof node.props?.onSave === 'function')[0];
    await form.props.onSave({ title: '新品咖啡', brief: '门店推广', reference_url: '', aspect_ratio: '9:16' }, ''); await view.flush();
    assert.equal(view.findAll(node => node.props?.id === 'workshop-project')[0].props.value, 'new-project');
    assert.equal(view.findAll(node => node.type === 'iframe')[0]?.props.src, 'http://127.0.0.1:8843/canvas/new-canvas');
    assert.equal(view.findAll(node => node.props?.role === 'dialog').length, 0);
  } finally { view.dispose(); }
});

test('archiving from project detail removes it from the active list and allows restoring it', async () => {
  let current = project();
  response = (url, options) => {
    if (url === '/api/workshop/status') return { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } };
    if (url.endsWith('/archive') && options.method === 'POST') {
      current = { ...current, archived_at: JSON.parse(options.body).archived ? '2026-11-02T03:00:00Z' : null };
    }
    return { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: [current] } : current };
  };
  const view = workshopView();
  try {
    await view.flush(); enterProject(view); await view.flush();
    await view.findAll(node => node.type === 'button' && node.props.children === '归档项目')[0].props.onClick(); await view.flush();
    assert.equal(view.findAll(node => node.type === 'button' && node.props.children === '恢复项目').length, 1);
    view.findAll(node => node.type === 'button' && node.props.children === '返回视频列表')[0].props.onClick(); await view.flush();
    assert.equal(view.findAll(node => node.props?.['data-project-id']).length, 0);
    view.findAll(node => node.props?.['aria-label'] === '查看归档视频')[0].props.onClick(); await view.flush();
    enterProject(view); await view.flush();
    await view.findAll(node => node.type === 'button' && node.props.children === '恢复项目')[0].props.onClick(); await view.flush();
    assert.equal(view.findAll(node => node.type === 'button' && node.props.children === '归档项目').length, 1);
  } finally { view.dispose(); }
});

test('late project updates never reopen detail after returning to the video list', async () => {
  let current = project();
  let finishArchive;
  response = (url, options) => {
    if (url === '/api/workshop/status') return { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } };
    if (url.endsWith('/archive') && options.method === 'POST') return new Promise(resolve => {
      finishArchive = () => { current = { ...current, archived_at: '2026-11-02T03:00:00Z' }; resolve({ ok: true, status: 200, data: current }); };
    });
    return { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: [current] } : current };
  };
  const view = workshopView();
  try {
    await view.flush(); enterProject(view); await view.flush();
    const lateExport = view.findAll(node => typeof node.type === 'function' && typeof node.props?.onOpenReview === 'function')[0].props.onUpdate;
    view.findAll(node => node.type === 'button' && node.props.children === '归档项目')[0].props.onClick(); await view.flush();
    view.findAll(node => node.type === 'button' && node.props.children === '返回视频列表')[0].props.onClick(); await view.flush();
    assert.equal(typeof finishArchive, 'function'); finishArchive(); await view.flush();
    assert.equal(view.findAll(node => node.props?.id === 'workshop-project').length, 0);
    lateExport({ ...current, revision: 5 }); await view.flush();
    assert.equal(view.findAll(node => node.props?.id === 'workshop-project').length, 0);
    assert.equal(view.findAll(node => node.props?.['aria-label'] === '视频列表').length, 1);
  } finally { view.dispose(); }
});

test('a creation response after cancelling its dialog saves data without navigating into it', async () => {
  let list = [project()];
  let finishCreate;
  const created = { ...project(), id: 'late-project', title: '迟到的新项目', deliverables: [] };
  response = (url, options) => {
    if (url === '/api/workshop/status') return { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } };
    if (url === '/api/workshop/projects' && options.method === 'POST') return new Promise(resolve => {
      finishCreate = () => { list = [created, ...list]; resolve({ ok: true, status: 201, data: created }); };
    });
    return { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: list } : created };
  };
  const view = workshopView();
  try {
    await view.flush(); view.findAll(node => node.props?.id === 'workshop-create-project')[0].props.onClick(); await view.flush();
    const form = view.findAll(node => typeof node.type === 'function' && typeof node.props?.onSave === 'function')[0];
    const pending = form.props.onSave({ title: created.title, brief: '', reference_url: '', aspect_ratio: '9:16' }, '');
    await view.flush(); form.props.onCancel(); await view.flush(); finishCreate(); await pending; await view.flush();
    assert.equal(view.findAll(node => node.props?.id === 'workshop-project').length, 0);
    assert.equal(view.findAll(node => node.props?.['data-project-id'] === created.id).length, 1);
  } finally { view.dispose(); }
});

test('simulated review never offers publication recording while current human approval does', async () => {
  let current = { ...project(), stage: 'simulated_reviewed', reviews: [{ id: 'r', revision: 4, artifact_sha256: 'latest', decision: 'approved', kind: 'simulated' }] };
  response = url => url === '/api/workshop/status'
    ? { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } }
    : { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: [current] } : current };
  const view = workshopView();
  try {
    await view.flush(); enterProject(view); await view.flush();
    const publicationButtons = () => view.findAll(node => node.type === 'button' && node.props.children === '记录已发布');
    assert.equal(publicationButtons().length, 0);
    current = { ...current, stage: 'approved', reviews: [{ id: 'r2', revision: 4, artifact_sha256: 'latest', decision: 'approved', kind: 'human' }] };
    view.poll(); await view.flush();
    assert.equal(publicationButtons().length, 1);
    publicationButtons()[0].props.onClick(); await view.flush();
    assert.equal(view.findAll(node => node.props?.role === 'dialog' && node.props['aria-label'] === '记录已发布视频').length, 1);
    assert.equal(view.findAll(node => node.type === 'input' && node.props.type === 'url' && node.props.required).length, 1);
  } finally { view.dispose(); }
});

test('explicitly entering delivered work opens review without mounting its editor even briefly', async () => {
  response = url => url === '/api/workshop/status'
    ? { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } }
    : { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: [project()] } : project() };
  const view = workshopView();
  try {
    await view.flush();
    enterProject(view); await view.flush();
    assert.equal(view.findAll(node => node.props?.id === 'workshop-review-panel')[0].props.hidden, false);
    assert.equal(view.findAll(node => node.type === 'iframe').length, 0);
    assert.ok(view.frameHistory.every(frames => frames.length === 0), 'editor mounted before review became active');
    view.findAll(node => node.props?.id === 'workshop-canvas-tab')[0].props.onClick();
    await view.flush();
    assert.equal(view.findAll(node => node.type === 'iframe')[0]?.props.src, 'http://127.0.0.1:8843/canvas/canvas-one');
  } finally { view.dispose(); }
});

test('switching projects selects review for delivered work and canvas for drafts without an implicit editor mount', async () => {
  const draft = { ...project(), id: 'draft', title: '新草稿', beeftv_canvas_id: 'canvas-draft', deliverables: [] };
  const delivered = project();
  response = url => url === '/api/workshop/status'
    ? { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } }
    : { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: [draft, delivered] }
      : url.endsWith('/draft') ? draft : delivered };
  const view = workshopView();
  const select = id => view.findAll(node => node.props?.id === 'workshop-project')[0].props.onChange({ target: { value: id } });
  const reviewVisible = () => !view.findAll(node => node.props?.id === 'workshop-review-panel')[0].props.hidden;
  try {
    await view.flush();
    enterProject(view, 'draft'); await view.flush();
    assert.equal(reviewVisible(), false);
    assert.equal(view.findAll(node => node.type === 'iframe')[0]?.props.src, 'http://127.0.0.1:8843/canvas/canvas-draft');
    let before = view.frameHistory.length;
    select(delivered.id); await view.flush();
    assert.equal(reviewVisible(), true);
    assert.ok(view.frameHistory.slice(before).every(frames => frames.length === 0));
    view.findAll(node => node.props?.id === 'workshop-canvas-tab')[0].props.onClick(); await view.flush();
    assert.equal(view.findAll(node => node.type === 'iframe')[0]?.props.src, 'http://127.0.0.1:8843/canvas/canvas-one');
    select(draft.id); await view.flush();
    assert.equal(reviewVisible(), false);
    before = view.frameHistory.length;
    select(delivered.id); await view.flush();
    assert.equal(reviewVisible(), true);
    assert.ok(view.frameHistory.slice(before).every(frames => frames.length === 0), 'returning to delivered work reopened its editor');
  } finally { view.dispose(); }
});

test('an established canvas stays mounted through a failed runtime probe, and explicit reload changes its key', async () => {
  let online = true;
  response = url => url === '/api/workshop/status'
    ? online ? { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/', version: 'v2.1.1' } }
      : { ok: false, status: 503, data: { detail: '暂时无法连接' } }
    : { ok: true, status: 200, data: url === '/api/workshop/projects' ? { projects: [project()] } : project() };
  const view = workshopView();
  try {
    await view.flush();
    enterProject(view); await view.flush();
    view.findAll(node => node.props?.id === 'workshop-canvas-tab')[0].props.onClick(); await view.flush();
    const original = view.findAll(node => node.type === 'iframe')[0];
    assert.equal(original?.props.src, 'http://127.0.0.1:8843/canvas/canvas-one');
    online = false;
    view.poll(); await view.flush();
    const retained = view.findAll(node => node.type === 'iframe')[0];
    assert.ok(retained, 'temporary status failure unmounted the open canvas');
    assert.equal(retained.key, original.key);
    assert.equal(retained.props.src, original.props.src);
    online = true;
    view.poll(); await view.flush();
    view.findAll(node => node.type === 'button' && node.props.children === '重新载入画布')[0].props.onClick();
    await view.flush();
    const reloaded = view.findAll(node => node.type === 'iframe')[0];
    assert.notEqual(reloaded.key, original.key);
  } finally { view.dispose(); }
});

test('the visible project stage follows enhanced canvas state and does not retain approval when refresh fails', async () => {
  const approved = { ...project(), stage: 'approved' };
  approved.reviews = [{ id: 'r1', revision: 4, artifact_sha256: 'latest', decision: 'approved', kind: 'human' }];
  let changed = false;
  let listAvailable = true;
  response = url => url === '/api/workshop/status'
    ? { ok: true, status: 200, data: { state: 'running', ui_url: 'http://127.0.0.1:8843/' } }
    : url === '/api/workshop/projects'
      ? listAvailable ? { ok: true, status: 200, data: { projects: [approved] } }
        : { ok: false, status: 503, data: { detail: '无法读取项目' } }
      : { ok: true, status: 200, data: { ...approved,
        engine_current_revision: changed ? 13 : 12, engine_dirty: changed } };
  const view = workshopView();
  const hasStage = label => view.findAll(node => node.type === 'span' && node.props.children === label).length > 0;
  try {
    await view.flush();
    enterProject(view); await view.flush();
    assert.equal(hasStage('人工审阅通过'), true);
    changed = true; view.poll(); await view.flush();
    assert.equal(hasStage('需重新导出'), true);
    assert.equal(hasStage('人工审阅通过'), false);
    changed = false; view.poll(); await view.flush();
    assert.equal(hasStage('人工审阅通过'), true);
    listAvailable = false; view.poll(); await view.flush();
    assert.equal(hasStage('画布版本待确认'), true);
    assert.equal(hasStage('人工审阅通过'), false);
  } finally { view.dispose(); }
});
