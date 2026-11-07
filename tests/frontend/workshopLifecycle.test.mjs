import { componentModule } from './agentMessagesHarness.mjs';
import { projectScope, runWithProjectScope } from './projectScopeHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/lib/workshop.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
} }).outputText;
const api = {};
runWithProjectScope(compiled, { exports: api, URL, AbortController, require: name => name.includes('scopedReadCache') ? componentModule('lib/scopedReadCache.ts') : projectScope });

const artifact = { id: 'delivery-1', path: 'sample/final.mp4', sha256: 'sha-current', revision: 3,
  engine_revision: 8, created_at: '2026-11-02T01:00:00Z' };
const base = (patch = {}) => ({ id: 'project-one', title: '家庭教育推广', brief: '亲子沟通课程',
  reference_url: '', aspect_ratio: '9:16', revision: 3, stage: 'draft', beeftv_canvas_id: '',
  deliverables: [], reviews: [], renders: [], updated_at: '2026-11-02T01:00:00Z', ...patch });
const reviewed = (kind = 'human', decision = 'approved', patch = {}) => base({ stage: 'approved',
  deliverables: [artifact], reviews: [{ id: 'review-1', revision: 3, artifact_id: artifact.id,
    artifact_sha256: artifact.sha256, kind, decision, notes: '', created_at: '2026-11-02T01:05:00Z' }], ...patch });
const receipt = (patch = {}) => ({ id: 'render-1', content_revision: 3, engine_revision: 8,
  status: 'queued', submission_state: 'confirmed', task_id: 'task-1', progress: 0,
  created_at: '2026-11-02T01:00:00Z', updated_at: '2026-11-02T01:00:00Z', ...patch });

test('a canvas connection does not turn a stored draft into an approved or published project', () => {
  assert.equal(api.projectLifecycle(base({ beeftv_canvas_id: 'canvas-1' })).key, 'draft');
});

test('a current queued export is shown as production before it has a delivery', () => {
  assert.equal(api.projectLifecycle(base({ renders: [receipt()] })).key, 'producing');
});

test('successful but uncollected export remains production rather than review-ready', () => {
  assert.equal(api.projectLifecycle(base({ renders: [receipt({ status: 'succeeded' })] })).key, 'producing');
});

test('a failed export stays in production with an actionable error detail', () => {
  const state = api.projectLifecycle(base({ renders: [receipt({ status: 'failed' })] }));
  assert.equal(state.key, 'producing');
  assert.equal(state.tone, 'danger');
  assert.ok(state.detail.includes('失败'));
  assert.equal(api.projectLifecycle(base({ renders: [receipt({ status: 'failed', content_revision: 2 })] })).key, 'draft');
});

test('simulated approval remains in the human-review queue', () => {
  assert.equal(api.projectLifecycle(reviewed('simulated')).key, 'pending_review');
});

test('human approval of the matching delivery makes it ready to publish', () => {
  assert.equal(api.projectLifecycle(reviewed()).key, 'ready_to_publish');
});

test('a stage label without actual human review cannot make a project ready to publish', () => {
  assert.equal(api.projectLifecycle(base({ stage: 'approved', deliverables: [artifact] })).key, 'pending_review');
});

test('changes requested for the current delivery route it into revisions', () => {
  assert.equal(api.projectLifecycle(reviewed('human', 'changes_requested')).key, 'producing');
});

test('an old review cannot approve a new delivery', () => {
  const project = reviewed(); project.reviews[0].artifact_sha256 = 'sha-old';
  assert.equal(api.projectLifecycle(project).key, 'pending_review');
});

test('content changes after delivery require a new cut', () => {
  assert.equal(api.projectLifecycle(reviewed('human', 'approved', { revision: 4, stage: 'editing' })).key, 'producing');
});

test('published requires a publication record matching the actual delivery and version', () => {
  const publication = { id: 'pub-1', revision: 3, artifact_id: artifact.id,
    artifact_sha256: artifact.sha256, platform: '视频号', url: 'https://example.com/post',
    published_at: '2026-11-02T02:00:00Z', created_at: '2026-11-02T02:01:00Z' };
  assert.equal(api.projectLifecycle(reviewed('human', 'approved', { publications: [publication] })).key, 'published');
  assert.equal(api.projectLifecycle(base({ stage: 'published', deliverables: [artifact] })).key, 'pending_review');
  assert.equal(api.projectLifecycle(reviewed('human', 'approved', {
    publications: [{ ...publication, artifact_sha256: 'sha-old' }],
  })).key, 'ready_to_publish');
});

test('archived projects retain their history while leaving active queues', () => {
  const archived = reviewed('human', 'approved', { archived_at: '2026-11-02T03:00:00Z' });
  assert.equal(api.projectLifecycle(archived).key, 'archived');
  assert.equal(api.filterWorkshopProjects([archived]).length, 0);
  assert.equal(api.filterWorkshopProjects([archived], '', 'archived').length, 1);
});

test('search and status filter combine and results sort by the actual update time', () => {
  const projects = [base({ id: 'older' }), base({ id: 'unrelated', title: '汽车营销', brief: '' }),
    reviewed('simulated', 'approved', { id: 'newer', updated_at: '2026-11-02T04:00:00Z' })];
  assert.deepEqual(Array.from(api.filterWorkshopProjects(projects, '亲子')).map(p => p.id), ['newer', 'older']);
  assert.deepEqual(Array.from(api.filterWorkshopProjects(projects, '家庭', 'pending_review')).map(p => p.id), ['newer']);
});

test('one production filter includes active jobs, requested edits and failed exports', () => {
  const projects = [base({ id: 'active', renders: [receipt()] }),
    reviewed('human', 'changes_requested', { id: 'needs-edit' }),
    base({ id: 'failed', renders: [receipt({ status: 'failed' })] }),
    base({ id: 'draft' })];
  assert.deepEqual(Array.from(api.filterWorkshopProjects(projects, '', 'producing')).map(p => p.id),
    ['active', 'needs-edit', 'failed']);
});
