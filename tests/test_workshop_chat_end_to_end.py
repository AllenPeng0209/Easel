"""Chat Host → direct model → native assets/shots → human review → platform dispatch."""
import json
import subprocess
import time

import httpx
import pytest

import web.app as web
from easel.video_provider import NativeVideoClient
from easel.video_workshop import WorkshopStore
from tests.test_chat_artifacts import artifact_http_environment
from tests.test_publication_workflow_http import publication_environment

pytestmark = pytest.mark.access_integration
_RUN = subprocess.run


@pytest.fixture
def video_source(tmp_path):
    source = tmp_path / 'test-provider.mp4'
    _RUN(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=64x112:r=10:d=1',
          '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
          '-c:a', 'aac', '-shortest', str(source)], check=True, capture_output=True)
    return source.read_bytes()


def test_complete_chat_production_and_single_workshop_approval(video_source, publication_environment, monkeypatch):
    from web import video_generation as bridge
    root, owner, _, _, sent = publication_environment
    monkeypatch.setattr(subprocess, 'run', _RUN)
    monkeypatch.setattr(web, '_chat_backend', lambda session: 'codex')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'workspace-write'})
    monkeypatch.setattr(bridge, 'generation_state_directory', lambda *args: root.with_name(root.name + '-generation-state') / 'jobs')
    submissions, deliveries = [], []
    def provider(request):
        assert request.headers['authorization'] == 'Bearer test-key'
        if request.method == 'POST':
            submissions.append(json.loads(request.content))
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=video_source)
        return httpx.Response(200, json={'id': 'video_' + 'e' * 32, 'status': 'completed'})
    client = NativeVideoClient(api_key='test-key', transport=httpx.MockTransport(provider))
    monkeypatch.setattr(bridge, 'NativeVideoClient', lambda **kwargs: client)

    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('native-studio-end-to-end')
            folder = request.cwd / 'outputs' / 'film'
            folder.mkdir()
            manifest = {'series': '家庭教育', 'episodes': [{'id': '01', 'title': '先听孩子说完',
                'brief': '用户通过对话交办的原生短剧', 'aspectRatio': '9:16', 'path': 'film/planned.mp4'}]}
            path = folder / 'series.json'
            path.write_text(json.dumps(manifest))
            planned = await request.workshop_delivery({'action': 'plan', 'manifestPath': 'film/series.json'})
            workshop_id = planned[0]['workshopId']
            spec = {'action': 'prepare', 'workshopId': workshop_id, 'clipId': 'shot-1',
                'modelId': 'easel-ppu-h3-local::minimax-h3-dense8', 'aspectRatio': '9:16',
                'resolution': '480p', 'seconds': 1, 'prompt': '母亲放下手机，听孩子说完。'}
            await request.video_generation(spec, 'prepare')
            for action in ['submit', 'collect']:
                collected = await request.video_generation({'action': action, 'workshopId': workshop_id, 'clipId': 'shot-1'}, action)
            assert collected['status'] == 'collected'
            episode = manifest['episodes'][0]
            episode.update(workshopId=workshop_id, path=collected['artifact']['path'],
                publication={'platform': 'douyin', 'title': '先听孩子说完', 'body': '家庭教育短剧', 'tags': '家庭教育', 'aiGenerated': True})
            path.write_text(json.dumps(manifest))
            delivered = await request.workshop_delivery({'action': 'deliver', 'manifestPath': 'film/series.json'})
            deliveries.extend(delivered)
            emit('token', '成片已自动进入视频工坊，等待人工审核。')

    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    with owner:
        response = owner.post('/api/chat/stream', json={'message': '调用视频制作 Skill，完成短剧后进工坊，审核后发到抖音。',
            'sessionId': 'studio-chat', 'turnId': 'studio-turn'})
        assert response.status_code == 200 and '成片已自动进入视频工坊' in response.text, response.text
        assert len(submissions) == 1 and not sent
        project = WorkshopStore(root / 'outputs').get(deliveries[0]['workshopId'])
        assert len(project['shots'][0]['candidates']) == 1
        assert len(project['deliverables']) == 1
        preparation = project['publication_preparation']
        assert preparation['subject']['account']['id'] == 'actual-account-1'
        reviewed = owner.post(f"/api/workshop/projects/{project['id']}/reviews", json={
            'expected_revision': project['revision'], 'artifact_id': project['deliverables'][-1]['id'],
            'kind': 'human', 'decision': 'approved', 'publication_approval_id': preparation['approval_id'],
            'publication_payload_hash': preparation['payload_hash']})
        assert reviewed.status_code == 200, reviewed.text
        deadline = time.monotonic() + 3
        while not sent and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(sent) == 1
        assert sent[0]['platform'] == 'douyin' and sent[0]['title'] == '先听孩子说完'
