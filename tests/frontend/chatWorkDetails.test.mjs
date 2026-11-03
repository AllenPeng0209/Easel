import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const defaults = { mode: 'compact', collapseTiming: 'completion' };
const preference = { mode: 'detailed', collapseTiming: 'next-input' };
const load = options => componentModule('lib/chatWorkDetails.ts', options);
const plain = value => JSON.parse(JSON.stringify(value));

test('restores display mode and independent fold timing only for the current actor and IP', () => {
  const storageValues = new Map();
  const inScope = { user: 'owner', project: 'studio', storageValues };
  assert.deepEqual(plain(load(inScope).loadWorkDetailsPreferences()), defaults);
  assert.equal(load(inScope).saveWorkDetailsPreferences(preference), true);
  assert.deepEqual(plain(load(inScope).loadWorkDetailsPreferences()), preference);
  assert.deepEqual(plain(load({ ...inScope, user: 'other' }).loadWorkDetailsPreferences()), defaults);
  assert.deepEqual(plain(load({ ...inScope, project: 'other' }).loadWorkDetailsPreferences()), defaults);
});

test('normalizes legacy detail aliases without writing them and recovers invalid settings', () => {
  const storageValues = new Map();
  const options = { user: 'owner', project: 'default', storageValues };
  const { loadWorkDetailsPreferences, saveWorkDetailsPreferences } = load(options);
  assert.equal(saveWorkDetailsPreferences(preference), true);
  const key = [...storageValues.keys()].find(key => key.includes('work_details'));
  assert.ok(key);
  for (const alias of ['normal', 'expanded']) {
    const stored = JSON.stringify({ mode: alias, collapseTiming: 'invalid' });
    storageValues.set(key, stored);
    assert.deepEqual(plain(loadWorkDetailsPreferences()), { mode: 'detailed', collapseTiming: 'completion' });
    assert.equal(storageValues.get(key), stored);
  }
  storageValues.set(key, 'malformed-json');
  assert.deepEqual(plain(loadWorkDetailsPreferences()), defaults);
  assert.equal(saveWorkDetailsPreferences({ mode: 'verbose', collapseTiming: 'completion' }), false);
  assert.equal(storageValues.get(key), 'malformed-json');
});

test('storage failure leaves the view usable with predictable defaults', () => {
  const unavailable = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  const { loadWorkDetailsPreferences, saveWorkDetailsPreferences } = load({ globals: { localStorage: unavailable } });
  assert.deepEqual(plain(loadWorkDetailsPreferences()), defaults);
  assert.equal(saveWorkDetailsPreferences(preference), false);
});
