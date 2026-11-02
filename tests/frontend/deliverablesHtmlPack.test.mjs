import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { componentModule } from './agentMessagesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const { parseHTML, DOMParser } = require('linkedom');
const ts = require('typescript');
const utf8 = text => new TextEncoder().encode(text);
const png = Uint8Array.from(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZ1cAAAAASUVORK5CYII=', 'base64'));
const source = '<!doctype html><html><head><link rel="stylesheet" href="./main.css"></head><body><img src="preview.png"><script src="app.js"></script></body></html>';
const id = 'a'.repeat(32);
const artifact = { url: `/api/projects/ip-one/chat/artifacts/${id}/content`, baseUrl: '/api/projects/ip-one/media/reports/demo/',
  storageMode: 'legacy-current', sourcePath: 'reports/demo/index.html' };

function environment(files = {}) {
  const { document } = parseHTML('<!doctype html><html><head></head><body></body></html>');
  const requests = [];
  const globals = { document, DOMParser, TextEncoder, TextDecoder, Uint8Array, ArrayBuffer,
    AbortController, AbortSignal, Blob, btoa, atob, Headers, Response, URLSearchParams, crypto,
    window: { location: { pathname: '/', origin: 'http://127.0.0.1:7860' }, dispatchEvent() {} },
    fetch: async (url, options) => {
      requests.push({ url, options });
      const query = new URL(url, 'http://127.0.0.1:7860').searchParams;
      const path = query.get('path');
      const payload = url.endsWith('/content') ? source : files[path];
      return payload === undefined ? new Response('not found', { status: 404 })
        : new Response(payload, { headers: { 'Content-Type': path?.endsWith('.png') ? 'image/png' : 'text/plain' } });
    },
  };
  const options = { user: 'owner', project: 'ip-one', globals };
  return { globals, options, requests };
}

function frameDocument(html) {
  const direct = new DOMParser().parseFromString(html, 'text/html');
  if (direct.querySelector('img')) return { document: direct, blobs: [] };
  let written;
  const blobs = [];
  const script = html.slice(html.indexOf('<script>') + 8, html.lastIndexOf('</script>'));
  runInNewContext(script, {
    document: { open() {}, write(value) { written = value; }, close() {} },
    URL: { createObjectURL(blob) { blobs.push(blob); return `blob:null/packed-${blobs.length}`; } },
    Blob, DOMParser, atob, TextDecoder, Uint8Array,
  });
  return { document: new DOMParser().parseFromString(written, 'text/html'), blobs };
}

// Invoke the actual HtmlArtifact load effect; its network boundary is controlled,
// while the source parsing, packing and bootstrap run as production code.
async function loadEffect(env) {
  const input = readFileSync(new URL('../../web/frontend/src/components/ArtifactGallery.tsx', import.meta.url), 'utf8');
  const parsed = ts.createSourceFile('ArtifactGallery.tsx', input, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const component = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'HtmlArtifact');
  const effect = component.body.statements.find(node => ts.isExpressionStatement(node)
    && ts.isCallExpression(node.expression) && node.expression.expression.getText(parsed) === 'useEffect').expression.arguments[0];
  const code = ts.transpileModule(`(${effect.getText(parsed)});`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const scope = componentModule('lib/projectScope.ts', env.options);
  const { previewBaseUrl } = componentModule('lib/artifacts.ts', env.options);
  const helperPath = new URL('../../web/frontend/src/lib/htmlPreview.ts', import.meta.url);
  const helpers = existsSync(helperPath) ? componentModule('lib/htmlPreview.ts', env.options) : {};
  return new Promise((resolve, reject) => {
    const execute = runInNewContext(code, { ...env.globals, ...helpers, ...artifact,
      projectFetch: scope.projectFetch, previewBaseUrl, token: { current: 'preview-token' },
      setSource: value => { if (value) resolve(value); }, setError: failed => { if (failed) reject(new Error('preview failed')); },
    });
    execute();
  });
}

test('opaque HTML frames receive image bytes as frame-owned blob URLs instead of cookie-protected media links', async () => {
  const env = environment({ 'preview.png': png, './main.css': utf8('body{color:navy}'), 'app.js': utf8('window.ready=true') });
  const html = await loadEffect(env);
  const frame = frameDocument(html);
  assert.match(frame.document.querySelector('img').getAttribute('src'), /^blob:null\//);
  const image = frame.blobs.find(blob => blob.type === 'image/png');
  assert.equal(image.size, png.byteLength);
  assert.deepEqual(Array.from(new Uint8Array(await image.arrayBuffer())), Array.from(png));
  assert.ok(env.requests.some(request => request.url.includes(`/artifacts/${id}/related?`)));
  assert.ok(env.requests.every(request => !request.url.includes('/media/')));
});

test('relative readers reject private paths, API schemes and traversal before any privileged parent fetch', async () => {
  const env = environment();
  const { createReadHtmlRelative } = componentModule('lib/htmlPreview.ts', env.options);
  const read = createReadHtmlRelative(artifact, new AbortController().signal);
  for (const reference of ['../../_login/secret.png', '../../_sessions/state.js', '../.secret/main.css',
    '../../cookies.json', '../../credentials.js', '../../../api/users.js', '/api/users.js',
    'https://evil.test/a.png', '//evil.test/a.js', 'file:///etc/passwd', 'data:text/javascript,1',
    '%2e%2e/%2e%2e/_login/a.png', '..\\..\\_login\\a.png', '../../_inbox/another/a.png']) {
    await assert.rejects(read(reference, new AbortController().signal), undefined, reference);
  }
  assert.equal(env.requests.length, 0);
});

test('classic scripts, styles and images pack once; external/module/CSS-recursive references never become parent reads', async () => {
  const env = environment();
  const { packHtml } = componentModule('vendor/deepseek-harness/html/pack.ts', env.options);
  const seen = [];
  const html = source.replace('</body>', '<img src="preview.png"><img src="https://cdn.test/a.png"><script type="module" src="module.js"></script></body>');
  const bundle = await packHtml(utf8(html), async reference => {
    seen.push(reference);
    return { data: reference.endsWith('.png') ? png : utf8(reference.endsWith('.css') ? '@import "secret.css";body{background:url(secret.png)}' : 'window.ready=true') };
  }, new AbortController().signal);
  assert.deepEqual(Array.from(seen), ['./main.css', 'preview.png', 'app.js']);
  assert.deepEqual(Array.from(bundle.assets, item => item.kind), ['stylesheet', 'image', 'script']);
  assert.equal(env.globals.document.querySelectorAll('img,script,link').length, 0);
});

test('failed dependency reads reject the complete pack instead of publishing a partial success', async () => {
  const env = environment();
  const { loadPackedHtmlArtifact } = componentModule('lib/htmlPreview.ts', env.options);
  await assert.rejects(loadPackedHtmlArtifact(artifact, 'preview-token', new AbortController().signal), /资源|预览|读取|not found/i);
  assert.equal(env.requests.filter(request => request.url.endsWith('/content')).length, 1);
});

test('packing enforces upstream per-resource size and cancellation boundaries', async () => {
  const env = environment();
  const { packHtml } = componentModule('vendor/deepseek-harness/html/pack.ts', env.options);
  await assert.rejects(packHtml(utf8('<img src="a.png">'), async () => ({ data: new Uint8Array(4 * 1024 * 1024 + 1) }),
    new AbortController().signal), /byte limit/);
  const controller = new AbortController(); controller.abort();
  let reads = 0;
  await assert.rejects(packHtml(utf8('<img src="a.png">'), async () => { reads++; return { data: png }; }, controller.signal));
  assert.equal(reads, 0);
});

test('public sibling assets use bound routes and exactly one URL decode for both new and legacy HTML', async () => {
  const env = environment({ '../shared/chart one.png': png, 'literal%20name.png': png });
  const { createReadHtmlRelative } = componentModule('lib/htmlPreview.ts', env.options);
  const signal = new AbortController().signal;
  await createReadHtmlRelative(artifact, signal)('../shared/chart%20one.png?v=1#image', signal);
  const legacy = { url: '/api/projects/ip-one/media/reports/demo/index.html' };
  await createReadHtmlRelative(legacy, signal)('literal%2520name.png', signal);
  const first = new URL(env.requests[0].url, env.globals.window.location.origin);
  assert.equal(first.pathname, `/api/projects/ip-one/chat/artifacts/${id}/related`);
  assert.equal(first.searchParams.get('path'), '../shared/chart one.png');
  const second = new URL(env.requests[1].url, env.globals.window.location.origin);
  assert.equal(second.pathname, '/api/projects/ip-one/chat/artifact-related');
  assert.equal(second.searchParams.get('document'), 'reports/demo/index.html');
  assert.equal(second.searchParams.get('path'), 'literal%20name.png');
  assert.ok(env.requests.every(request => request.options.redirect === 'error'
    && request.options.headers.get('X-Easel-User') === 'owner'));
});

test('an author base keeps upstream browser semantics without any parent dependency reads', async () => {
  const env = environment();
  const { packHtml } = componentModule('vendor/deepseek-harness/html/pack.ts', env.options);
  const { createHtmlDocument } = componentModule('vendor/deepseek-harness/html/bootstrap.ts', env.options);
  let reads = 0;
  const data = utf8('<!doctype html><html><head><base href="https://cdn.test/assets/"></head><body><img src="cover.png"></body></html>');
  const bundle = await packHtml(data, async () => { reads++; return { data: png }; }, new AbortController().signal);
  assert.equal(reads, 0);
  const frame = frameDocument(createHtmlDocument({ ...bundle, preview: { baseUrl: 'http://127.0.0.1:7860/api/media/reports/', script: '' } }));
  assert.equal(frame.document.querySelector('base').getAttribute('href'), 'https://cdn.test/assets/');
  assert.equal(frame.document.querySelector('img').getAttribute('src'), 'cover.png');
});

test('non-HTML APIs, cross-IP source URLs and private legacy documents are rejected before root fetch', async () => {
  const env = environment();
  const { loadPackedHtmlArtifact } = componentModule('lib/htmlPreview.ts', env.options);
  for (const url of ['/api/settings', '/api/projects/ip-two/media/public/index.html',
    '/api/projects/ip-one/media/_login/secret.html', '/api/projects/ip-one/media/reports/../secret.html',
    'https://evil.test/index.html']) {
    await assert.rejects(loadPackedHtmlArtifact({ url }, 'preview-token', new AbortController().signal));
  }
  assert.equal(env.requests.length, 0);
});

test('size reporting cannot proxy file requests and accepts failure only from its current frame and token', () => {
  const source = readFileSync(new URL('../../web/frontend/src/components/ArtifactGallery.tsx', import.meta.url), 'utf8');
  const parsed = ts.createSourceFile('ArtifactGallery.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let resize;
  const visit = node => {
    if (ts.isVariableDeclaration(node) && node.name.getText(parsed) === 'resize') resize = node.initializer;
    ts.forEachChild(node, visit);
  };
  visit(parsed);
  const code = ts.transpileModule(`(${resize.getText(parsed)});`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const current = {};
  const errors = [];
  const bounds = [];
  const receive = runInNewContext(code, { frame: { current: { contentWindow: current } }, token: { current: 'own-token' },
    setError: value => errors.push(value), setBounds: update => bounds.push(update({ width: 1, height: 1 })), Number,
  });
  receive({ source: current, data: { type: 'read-file', token: 'own-token', path: '_login/secret/cookies' } });
  receive({ source: {}, data: { type: 'easel-artifact-preview-error', token: 'own-token' } });
  receive({ source: current, data: { type: 'easel-artifact-preview-error', token: 'wrong-token' } });
  receive({ source: current, data: { type: 'easel-artifact-size', token: 'own-token', width: 999999, height: 3 } });
  assert.equal(errors.length, 0);
  assert.equal(bounds.length, 0);
  receive({ source: current, data: { type: 'easel-artifact-preview-error', token: 'own-token' } });
  assert.deepEqual(errors, [true]);
});
