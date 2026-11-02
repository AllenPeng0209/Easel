import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');

// A small DOM boundary double: event registration, containment, focus and visibility
// remain observable. The real copied hook and its composition/focus modules execute.
function modal() {
  const listeners = new Map();
  const doc = { activeElement: null,
    addEventListener(name, fn) { const items = listeners.get(name) || new Set(); items.add(fn); listeners.set(name, items); },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
    querySelectorAll: () => [dialog], defaultView: { addEventListener() {}, removeEventListener() {} },
  };
  class Element {
    constructor(name) { this.name = name; this.ownerDocument = doc; this.isConnected = true; this.attributes = new Map(); }
    focus() { doc.activeElement = this; }
    addEventListener() {} removeEventListener() {}
    setAttribute(key, value) { this.attributes.set(key, value); }
    removeAttribute(key) { this.attributes.delete(key); }
    matches() { return doc.activeElement === this; }
    closest() { return null; }
  }
  const opener = new Element('opener'), first = new Element('first'), last = new Element('last'), dialog = new Element('dialog');
  dialog.querySelector = () => first;
  dialog.querySelectorAll = () => [first, last];
  dialog.contains = element => element === dialog || element === first || element === last;
  opener.focus();
  const cleanups = [], cache = new Map();
  const hooks = { useRef: value => ({ current: value }), useLayoutEffect: effect => { const cleanup = effect(); if (cleanup) cleanups.push(cleanup); } };
  const load = path => {
    if (cache.has(path)) return cache.get(path);
    const exports = {}; cache.set(path, exports);
    const code = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023 } }).outputText;
    runInNewContext(code, { exports, HTMLElement: Element, require: name => name === 'react' ? hooks : load(resolve(dirname(path), `${name}.ts`)) });
    return exports;
  };
  const module = load(fileURLToPath(new URL('../../web/frontend/src/vendor/deepseek-harness/navigation/useModalLayer.ts', import.meta.url)));
  let closes = 0;
  module.useModalLayer({ current: dialog }, true, () => { closes += 1; });
  return { first, last, opener, doc, closeCount: () => closes, cleanup: () => cleanups.forEach(fn => fn()),
    dispatch(name, fields = {}) { const event = { key: '', defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...fields };
      for (const listener of listeners.get(name) || []) listener(event); return event; },
    listenerCount: () => [...listeners.values()].reduce((count, set) => count + set.size, 0),
  };
}

test('drawer modal traps forward/reverse Tab and restores the invoking control on close', () => {
  const ui = modal();
  assert.equal(ui.doc.activeElement, ui.first);
  ui.last.focus();
  assert.equal(ui.dispatch('keydown', { key: 'Tab' }).defaultPrevented, true);
  assert.equal(ui.doc.activeElement, ui.first);
  ui.dispatch('keydown', { key: 'Tab', shiftKey: true });
  assert.equal(ui.doc.activeElement, ui.last);
  ui.cleanup();
  assert.equal(ui.doc.activeElement, ui.opener);
  assert.equal(ui.listenerCount(), 0);
});

test('Escape closes only after composition is fully released, and repeat does not duplicate close', () => {
  const ui = modal();
  ui.dispatch('compositionstart');
  ui.dispatch('keydown', { key: 'Escape' });
  assert.equal(ui.closeCount(), 0);
  ui.dispatch('compositionend');
  ui.dispatch('keydown', { key: 'Escape' });
  assert.equal(ui.closeCount(), 0, 'late IME closing key must not close navigation');
  ui.dispatch('keyup');
  ui.dispatch('keydown', { key: 'Escape', repeat: true });
  assert.equal(ui.closeCount(), 0);
  ui.dispatch('keydown', { key: 'Escape' });
  assert.equal(ui.closeCount(), 1);
  ui.cleanup();
});
