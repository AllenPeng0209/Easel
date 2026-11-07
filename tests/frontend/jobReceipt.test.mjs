import assert from 'node:assert/strict';
import { test } from 'node:test';

test('accepted work returns its stable receipt before execution completes', async () => {
  const { jobReceipt } = await import(new URL('../../integrations/huobao-drama/source/frontend/app/utils/job-receipt.js', import.meta.url));
  let resolveJob, calls = 0;
  const receipt = jobReceipt({ started: true }, { id: 'durable-original-job' }, async job => {
    assert.equal(job.id, 'durable-original-job'); calls++;
    await new Promise(resolve => { resolveJob = resolve; });
  });
  assert.equal(receipt.job.id, 'durable-original-job');
  assert.equal(calls, 0);
  const first = receipt.completion(), second = receipt.completion();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 1);
  resolveJob();
  await Promise.all([first, second]);
});

test('a direct provider result completes without starting a Host task poll', async () => {
  const { jobReceipt } = await import(new URL('../../integrations/huobao-drama/source/frontend/app/utils/job-receipt.js', import.meta.url));
  const receipt = jobReceipt({ prompt: 'saved result' }, undefined, () => { throw new Error('Unexpected poll'); });
  assert.equal(receipt.data.prompt, 'saved result');
  await receipt.completion();
});
