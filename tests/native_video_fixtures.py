"""Direct model boundary fixture shared by native Host contract tests."""
import json
import httpx
import pytest
from easel.video_provider import NativeVideoClient
from easel.video_generation import VideoGenerationService
from easel.video_workshop import WorkshopStore

class Backend:
    def __init__(self):
        self.submitted = []
    def handle(self, request):
        if request.method == 'POST':
            self.submitted.append(json.loads(request.content))
        return httpx.Response(200, json={'id': 'video_' + '1' * 32, 'status': 'queued'})

@pytest.fixture
def setup(tmp_path):
    backend = Backend()
    outputs = tmp_path / 'workspace' / 'outputs'
    store = WorkshopStore(outputs)
    project = store.create({'title': 'Native video', 'aspect_ratio': '9:16'})
    client = NativeVideoClient(api_key='test-only', transport=httpx.MockTransport(backend.handle))
    service = VideoGenerationService(outputs=outputs, state_directory=tmp_path / 'host-state', client=client,
        actor_id='owner', ip='default', session_id='host-session', authorize=lambda: None, repository=outputs.parent)
    payload = {'action': 'prepare', 'workshopId': project['id'], 'clipId': 'first',
        'modelId': 'easel-ppu-h3-local::minimax-h3-dense8', 'aspectRatio': '9:16',
        'resolution': '720p', 'seconds': 5, 'prompt': 'A mother listens to her child.'}
    return store, service, backend, outputs, project, payload, client

def action(payload, name):
    return {'action': name, 'workshopId': payload['workshopId'], 'clipId': payload['clipId']}
