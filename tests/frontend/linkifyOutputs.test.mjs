import { componentModule } from './agentMessagesHarness.mjs';
import { projectScope, runWithProjectScope } from './projectScopeHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load(name, imports = {}) {
  const source = readFileSync(new URL(`../../web/frontend/src/lib/${name}.ts`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  runWithProjectScope(outputText, { exports, require: id => id === './projectScope' ? projectScope : id === './chatCatalogSync' ? componentModule('lib/chatCatalogSync.ts') : id === './scopedReadCache' ? componentModule('lib/scopedReadCache.ts') : id === './performance' ? componentModule('lib/performance.ts') : imports[id], window: { location: { pathname: '/' } } });
  return exports;
}
const api = load('api');
const { linkifyOutputs } = load('linkifyOutputs', { './api': api, marked: require('marked') });

test('absolute Markdown report links retain their label and open the content preview', () => {
  const input = '[查看复盘](/Users/example/Easel/outputs/示例项目/报告.md)';
  const expected = '[查看复盘](#/outputs/示例项目/报告.md)';
  const actual = linkifyOutputs(input);
  assert.equal(actual, expected.replace(/示例项目|报告.md/g, encodeURIComponent));
  assert.equal(linkifyOutputs(actual), actual);
});

test('images and angle-wrapped paths with spaces resolve through media or content routes', () => {
  assert.equal(linkifyOutputs('![图](/Users/example/Easel/outputs/a/cover.png)'), '![图](/api/media/a/cover.png)');
  assert.equal(linkifyOutputs('[报告](</Users/example/Easel/outputs/a b/report.md>)'), '[报告](#/outputs/a%20b/report.md)');
  assert.equal(linkifyOutputs('[报告](outputs/a/report.md)'), '[报告](#/outputs/a/report.md)');
});

test('external URLs, commands, converted links and traversal targets remain unchanged', () => {
  for (const input of [
    '[外部](https://example.com/outputs/report.md)',
    '[外部](//example.com/outputs/report.md)',
    '```\n[命令](/Users/example/Easel/outputs/a.md)\n```',
    '`cat outputs/a.md`',
    '[已处理](/api/media/a.md)',
    '[非法](outputs/../secret.md)',
    '[非法](outputs/%2e%2e/secret.md)',
    '[非法](outputs/a/./report.md)',
    '[损坏](outputs/%ZZ.md)',
    '[损坏](outputs/\uD800.md)',
  ]) assert.equal(linkifyOutputs(input), input);
});

test('Windows paths and encoded file names resolve without corrupting their destination', () => {
  assert.equal(linkifyOutputs('[报告](C:\\响芽\\outputs\\demo\\report.md)'), '[报告](#/outputs/demo/report.md)');
  assert.equal(linkifyOutputs('[报告](outputs/demo/a%20b.md)'), '[报告](#/outputs/demo/a%20b.md)');
  assert.equal(linkifyOutputs('[目录](outputs/demo/)'), '[目录](#/outputs/demo)');
});

test('multiple previewable Markdown output links resolve as media for gallery detection', () => {
  const input = '[卡片](outputs/cards/one.png) [网页](outputs/cards/two.html) [视频](outputs/cards/three.mp4)';
  assert.equal(linkifyOutputs(input),
    '[卡片](/api/media/cards/one.png) [网页](/api/media/cards/two.html) [视频](/api/media/cards/three.mp4)');
  assert.equal(linkifyOutputs('[PDF](</data/project/Easel/outputs/a b/card.pdf>)'),
    '[PDF](/api/media/a%20b/card.pdf)');
});

test('settlement preserves tilde, long, and unfinished fenced code byte-for-byte', () => {
  for (const fence of [
    '~~~bash\nls outputs/demo/a.png\n~~~',
    '````bash\n```\nls outputs/demo/a.png\n````',
    '   ~~~~~bash\r\n   ls outputs/demo/a.png\r\n  ~~~~~~ \r\n',
  ]) {
    assert.equal(linkifyOutputs(`outputs/demo/b.png\n\n${fence}\n\noutputs/demo/c.png`),
      `![b.png](/api/media/demo/b.png)\n\n${fence}\n\n![c.png](/api/media/demo/c.png)`);
  }
  for (const open of ['```bash\nls outputs/demo/a.png', '~~~~\n```\nls outputs/demo/a.png']) {
    assert.equal(linkifyOutputs(`outputs/demo/b.png\n\n${open}`), `![b.png](/api/media/demo/b.png)\n\n${open}`);
  }
});

test('settlement leaves fences inside quotes and lists intact, preserving adjacent prose links', () => {
  for (const nested of [
    '> ~~~bash\n> ls outputs/demo/a.png\n> ~~~',
    '- ````bash\n  ```\n  ls outputs/demo/a.png\n  ````',
    '> 1. ~~~bash\r\n>    ls outputs/demo/a.png\r\n>    ~~~',
    '- example\n\n  ~~~bash\n  ls outputs/demo/a.png',
  ]) {
    assert.equal(linkifyOutputs(`outputs/demo/b.png\n\n${nested}\n\noutputs/demo/c.png`),
      `![b.png](/api/media/demo/b.png)\n\n${nested}\n\n![c.png](/api/media/demo/c.png)`);
  }
});
