import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

async function view(mobile, verify, width = 900) {
  const { window, document } = parseHTML('<html><body><div class="chat-deliverable-layout" id="root"></div></body></html>');
  const parent = document.getElementById('root');
  parent.getBoundingClientRect = () => ({ width });
  const media = { matches: mobile, addEventListener() {}, removeEventListener() {} };
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true, writable: true },
    document: { value: document, configurable: true, writable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true, writable: true },
  });
  const root = require('react-dom/client').createRoot(parent);
  try {
    const ts = require('typescript');
    const source = readFileSync(new URL('../../web/frontend/src/components/DeliverablePreviewPane.tsx', import.meta.url), 'utf8');
    const code = ts.transpileModule(source, { compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    const exports = {};
    runInNewContext(code, { exports, window: { matchMedia: () => media }, document,
      require(name) {
        if (name === './DeliverablePreviewBody') return ({ file }) => React.createElement('p', null, 'Preview ' + file.artifactId);
        if (name.endsWith('.css')) return {};
        return require(name);
      },
    });
    let closes = 0;
    const show = open => React.act(async () => root.render(React.createElement(React.Fragment, null,
      React.createElement('div', { className: 'chat-page' }, 'Original chat'),
      open && React.createElement(exports.default, {
        file: { name: '报告.md', artifactId: 'a'.repeat(32) }, onClose: () => { closes++; },
      }))));
    await show(true);
    const dispatch = (target, type, fields) => React.act(async () => {
      const event = new window.Event(type, { bubbles: true, cancelable: true });
      for (const [key, value] of Object.entries(fields)) Object.defineProperty(event, key, { value });
      target.dispatchEvent(event);
    });
    await verify({ parent, dispatch, show, get closes() { return closes; } });
  } finally {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
}

test('desktop preview passes the fixed artifact to its body and supports bounded keyboard and pointer resizing', async () => {
  await view(false, async ({ parent, dispatch }) => {
    assert.match(parent.textContent, /报告\.md/);
    assert.match(parent.textContent, /Preview a{32}/);
    assert.ok(!parent.querySelector('.chat-page').hasAttribute('inert'));
    const separator = parent.querySelector('[role="separator"]');
    assert.ok(separator);
    const value = () => Number(separator.getAttribute('aria-valuenow'));
    const initial = value();
    await dispatch(separator, 'keydown', { key: 'ArrowLeft' });
    assert.ok(value() > initial);
    await dispatch(separator, 'keydown', { key: 'Home' });
    assert.equal(value(), Number(separator.getAttribute('aria-valuemin')));
    separator.setPointerCapture = () => {};
    separator.releasePointerCapture = () => {};
    separator.hasPointerCapture = () => true;
    await dispatch(separator, 'pointerdown', { button: 0, pointerId: 2, clientX: 500 });
    await dispatch(separator, 'pointermove', { pointerId: 2, clientX: 400 });
    assert.equal(value(), Number(separator.getAttribute('aria-valuemin')) + 100);
    await dispatch(separator, 'pointermove', { pointerId: 2, clientX: -1000 });
    assert.equal(value(), Number(separator.getAttribute('aria-valuemax')));
    await dispatch(separator, 'pointercancel', { pointerId: 2 });
    await dispatch(separator, 'pointermove', { pointerId: 2, clientX: 500 });
    assert.equal(value(), Number(separator.getAttribute('aria-valuemax')));
  });
});

test('mobile preview has dialog semantics and supports Back and Escape', async () => {
  await view(true, async ui => {
    const pane = ui.parent.querySelector('[role="dialog"]');
    assert.ok(pane);
    assert.equal(pane.getAttribute('aria-modal'), 'true');
    assert.ok(ui.parent.querySelector('.chat-page').hasAttribute('inert'));
    assert.ok(!pane.querySelector('[role="separator"]'));
    const back = pane.querySelector('button[aria-label="返回对话"]');
    assert.ok(back);
    await ui.dispatch(back, 'keydown', { key: 'Escape' });
    assert.equal(ui.closes, 1);
    await React.act(async () => back.click());
    assert.equal(ui.closes, 2);
    await ui.show(false);
    assert.ok(!ui.parent.querySelector('.chat-page').hasAttribute('inert'));
  });
});

test('a narrow chat layout uses fullscreen even at a desktop viewport', async () => {
  await view(false, async ({ parent }) => {
    assert.ok(parent.querySelector('.deliverable-preview-pane.is-fullscreen'));
    assert.ok(parent.querySelector('.chat-page').hasAttribute('inert'));
  }, 640);
});
