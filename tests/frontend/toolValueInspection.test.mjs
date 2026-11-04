import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const inspect = value => componentModule('lib/toolValueInspection.ts').inspectToolValue(value);

test('missing data differs from explicit null, and nested payloads keep their JSON types', () => {
  assert.equal(inspect(undefined).kind, 'missing');
  assert.equal(inspect(null).text, 'null');
  const input = inspect('{"media":[{"id":4,"enabled":false}],"task_id":"render-234"}');
  assert.equal(input.kind, 'json');
  assert.deepEqual(JSON.parse(input.text), { media: [{ id: 4, enabled: false }], task_id: 'render-234' });
  assert.deepEqual(Array.from(input.taskIds), ['render-234']);
});

test('copy and tree use the same sanitized projection, without untyped task IDs', () => {
  const fixturePassword = 'very-private-password';
  const fixtureKey = ['api_key', JSON.stringify('plain-secret-value')].join('=');
  const result = inspect({
    password: fixturePassword,
    headers: { authorization: 'Bearer private-token', cookie: 'cookie-private' },
    responses: [{ taskId: 'render-1' }, { task_id: 'render-1' }, { id: 'false-task' },
      { taskId: 'sk-not-for-copy-abcdefg' }],
    command: `KEYWORD=ok ${fixtureKey} Bearer header-private-123 --password "private flag value"`,
    header: 'Cookie: first=private-cookie-one; second=private-cookie-two',
    nestedText: '{"refresh_token":"raw-refresh-value"}',
    diagnostic: '-----BEGIN PRIVATE KEY-----\nPRIVATE-MATERIAL\n-----END PRIVATE KEY-----',
  });
  assert.equal(result.kind, 'json');
  for (const secret of ['very-private-password','private-token','cookie-private','plain-secret-value',
    'header-private-123','private flag value','private-cookie-one','private-cookie-two','raw-refresh-value','PRIVATE-MATERIAL','sk-not-for-copy']) {
    assert.ok(!result.text.includes(secret), secret);
    assert.ok(!JSON.stringify(result.value).includes(secret), secret);
  }
  assert.deepEqual(Array.from(result.taskIds), ['render-1']);
});

test('oversized JSON strings and plain output obey the same size and copy limits', () => {
  const omittedFixture = 'sensitive-tail';
  for (const value of [
    JSON.stringify({ input: 'a'.repeat(60000), password: omittedFixture }),
    'line\n'.repeat(40000),
    { list: Array.from({ length: 5000 }, () => ({ row: 'a'.repeat(150) })) },
  ]) {
    const result = inspect(value);
    assert.equal(result.truncated, true);
    assert.ok(result.text.length <= 16000);
    assert.ok(!result.text.includes('sensitive-tail'));
  }
});

test('safe recorded task IDs remain available when the display falls back to clipped text', () => {
  const result = inspect({ taskId: 'render-long', output: 'a'.repeat(20000) });
  assert.equal(result.truncated, true);
  assert.deepEqual(Array.from(result.taskIds), ['render-long']);
});

test('recursive or oversized records stop at bounded depth and redact credentials in key names', () => {
  const nested = {};
  nested.cycle = nested;
  nested['https://someone:key-value@host.invalid'] = 'link';
  const result = inspect(nested);
  assert.equal(result.truncated, true);
  assert.ok(!result.text.includes('key-value'));
  assert.ok(result.text.length <= 16000);
});
