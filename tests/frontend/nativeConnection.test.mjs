import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const settle = () => new Promise(resolve => setImmediate(resolve));
const compile = name => ts.transpileModule(readFileSync(new URL(
  `../../web/frontend/src/components/${name}.tsx`, import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const walk = tree => {
  const found = [];
  const visit = node => {
    if (!node || typeof node !== 'object') return;
    found.push(node);
    [node.props?.children].flat(Infinity).forEach(visit);
  };
  visit(tree);
  return found;
};
const text = node => typeof node === 'string' ? node : node && typeof node === 'object'
  ? [node.props?.children].flat(Infinity).map(text).join('') : '';

function view(name, props, api = {}, runEffects = false) {
  let cursor = 0;
  const slots = [], queued = [];
  const same = (left, right) => left && right && left.length === right.length
    && left.every((value, index) => Object.is(value, right[index]));
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, value => {
        slots[index].value = typeof value === 'function' ? value(slots[index].value) : value;
      }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useCallback(callback, dependencies) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].dependencies, dependencies)) slots[index] = { value: callback, dependencies };
      return slots[index].value;
    },
    useEffect(effect, dependencies) {
      const index = cursor++;
      if (!runEffects || (slots[index] && same(slots[index].dependencies, dependencies))) return;
      const previous = slots[index];
      slots[index] = { dependencies };
      queued.push(() => { previous?.cleanup?.(); slots[index].cleanup = effect(); });
    },
  };
  const exports = {};
  const jsx = (type, props) => ({ type, props });
  runInNewContext(compile(name), { exports, Error, Date, setTimeout, clearTimeout,
    localStorage: { getItem: () => null },
    window: { addEventListener() {}, removeEventListener() {} },
    require: path => path === 'react' ? hooks : path === 'react/jsx-runtime' ? { jsx, jsxs: jsx, Fragment:'fragment' }
      : path === '../lib/api' ? api : path === './HarnessSettings' ? { __esModule:true, default: 'harness-settings' }
      : path === './SettingsSelect' ? { __esModule:true, default:'settings-select', OptionsDropdown:'options-dropdown' }
      : new Proxy({}, { get: (_, key) => key === 'default' ? path : key }),
  });
  return {
    render() { cursor = 0; const tree = exports.default(props); while (queued.length) queued.shift()(); return walk(tree); },
  };
}

const legacyBanner = '还没有配置模型 —— 填一个 API Key 才能开始对话。';

for (const runtime of ['codex', 'opencode', 'acp']) {
  test(`${runtime} preview replaces the API-key blocker with native-login guidance`, () => {
    const panel = view('SettingsPanel', { onClose() {}, banner:legacyBanner });
    const engine = panel.render().find(node => node.type === 'harness-settings');
    engine.props.onChange('openclaw');
    engine.props.onSelect(runtime);
    const nodes = panel.render();
    const banner = nodes.filter(node => node.props?.role === 'status').map(text).join('');
    assert.doesNotMatch(banner, /填一个 API Key 才能/);
    assert.match(banner, /原生登录/);
    assert.match(banner, /保存底座/);
    assert.ok(!nodes.some(node => node.type === 'strong' && text(node) === 'OpenClaw API 配置'));
  });
}

test('an active native engine requires a real conversation check rather than an API-key form', () => {
  const panel = view('SettingsPanel', { onClose() {}, banner:legacyBanner });
  const engine = panel.render().find(node => node.type === 'harness-settings');
  engine.props.onChange('codex');
  engine.props.onSelect('codex');
  const banner = panel.render().filter(node => node.props?.role === 'status').map(text).join('');
  assert.doesNotMatch(banner, /填一个 API Key 才能/);
  assert.match(banner, /新建对话/);
  assert.match(banner, /真实回复/);
});

test('API and OpenClaw selections retain their actual provider configuration reminder', () => {
  for (const runtime of ['api', 'openclaw']) {
    const panel = view('SettingsPanel', { onClose() {}, banner:legacyBanner });
    const engine = panel.render().find(node => node.type === 'harness-settings');
    engine.props.onChange(runtime);
    engine.props.onSelect(runtime);
    const banner = panel.render().filter(node => node.props?.role === 'status').map(text).join('');
    assert.equal(banner, legacyBanner);
  }
});

test('native engine preview does not ask the user to save an unrelated API configuration', async () => {
  const config = { runtime:'openclaw', models:{codex:''}, sandbox:'workspace-write', locked:false,
    agents:[{id:'openclaw',label:'OpenClaw',installed:true},{id:'codex',label:'Codex',installed:true}] };
  const form = view('HarnessSettings', { onChange() {}, onSelect() {} }, {
    fetchHarnessSettings: async () => config,
  }, true);
  form.render(); await settle();
  form.render().find(node => node.type === 'settings-select' && node.props.label === '执行引擎').props.onChange('codex');
  const preview = form.render().filter(node => node.type === 'p' && /当前为配置预览/.test(text(node))).map(text).join('');
  assert.doesNotMatch(preview, /再保存下方 API 配置/);
  assert.match(preview, /保存底座/);
  assert.match(preview, /新建对话/);
});

test('a rejected native catalog request ends loading while preserving the chosen model', async () => {
  let reject;
  const config = {runtime:'codex',models:{codex:'kept-model'},sandbox:'read-only',locked:false,
    agents:[{id:'codex',label:'Codex',installed:true}]};
  const form = view('HarnessSettings', {onChange() {},onSelect() {}}, {
    fetchHarnessSettings: async () => config,
    fetchHarnessModels: () => new Promise((_, fail) => {reject = fail;}),
  }, true);
  form.render(); await settle();
  form.render().find(node => node.type === 'button' && text(node) === '拉取模型').props.onClick();
  assert.ok(form.render().some(node => node.type === 'button' && text(node) === '读取中…' && node.props.disabled));
  reject(new Error('模型目录暂时不可用'));
  await settle();
  const nodes = form.render();
  assert.ok(nodes.some(node => node.type === 'button' && text(node) === '拉取模型' && !node.props.disabled));
  assert.ok(nodes.some(node => node.props?.role === 'status' && /模型目录暂时不可用/.test(text(node))));
  assert.equal(nodes.find(node => node.type === 'input').props.value, 'kept-model');
});
