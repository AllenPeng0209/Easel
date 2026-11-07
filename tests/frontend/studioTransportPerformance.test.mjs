import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const folder = '../../integrations/huobao-drama/source/frontend/app/utils/';

test('episode assets start independent reads together and retain successful modules', async () => {
  const { loadEpisodeAssets } = await import(new URL(folder + 'studio-bootstrap.js', import.meta.url));
  let release;
  const calls = [];
  const result = loadEpisodeAssets({
    characters: async id => { calls.push(['characters', id]); return new Promise(resolve => { release = resolve; }); },
    scenes: async id => { calls.push(['scenes', id]); return ['room']; },
    props: async id => { calls.push(['props', id]); throw new Error('one module unavailable'); },
    storyboards: async id => { calls.push(['storyboards', id]); return ['opening']; },
  }, 12);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, [['characters', 12], ['scenes', 12], ['props', 12], ['storyboards', 12]]);
  release(['actor']);
  assert.deepEqual(await result, { characters: ['actor'], scenes: ['room'], props: [], storyboards: ['opening'] });
});

test('playback variants are explicit and external or original download URLs stay unchanged', async () => {
  const { playbackMediaUrl, thumbnailMediaUrl } = await import(new URL(folder + 'media-urls.js', import.meta.url));
  const prefix = '/api/workshop/projects/' + 'a'.repeat(32);
  const original = prefix + '/engine/static/videos/task-2.mp4';
  assert.equal(playbackMediaUrl(original, prefix, 'https://chirpiva.com'), original + '?playback=original');
  assert.equal(playbackMediaUrl(original, prefix, 'https://chirpiva.com', 'low'), original + '?playback=low');
  assert.equal(playbackMediaUrl('https://provider.test/video.mp4?signature=fixture', prefix, 'https://chirpiva.com'), 'https://provider.test/video.mp4?signature=fixture');
  assert.equal(thumbnailMediaUrl(prefix + '/engine/static/poster.jpg', prefix, 'https://chirpiva.com', 192), prefix + '/engine/static/poster.jpg?thumb=1&w=192');
  assert.equal(thumbnailMediaUrl('/api/another-ip/static/poster.jpg', prefix, 'https://chirpiva.com'), '/api/another-ip/static/poster.jpg');
});

test('output preview helpers opt into playback while download helpers retain original URLs', () => {
  const api = componentModule('lib/api.ts');
  const workshop = componentModule('lib/workshop.ts');
  assert.equal(api.mediaUrl('film/final.mp4', true), '/api/media/film/final.mp4?playback=original');
  assert.equal(api.mediaUrl('film/final.mp4'), '/api/media/film/final.mp4');
  assert.equal(workshop.workshopMediaUrl('film/final.mp4', true), '/api/media/film/final.mp4?playback=original');
  assert.equal(workshop.workshopMediaUrl('film/final.mp4'), '/api/media/film/final.mp4');
});
