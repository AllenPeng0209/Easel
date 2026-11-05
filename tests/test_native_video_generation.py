"""Direct PPU admission must preserve one purchase and exact local delivery."""
import importlib.util
import json
import subprocess

import httpx
import pytest

from easel.video_generation import VideoGenerationService
from easel.video_workshop import WorkshopStore
from tests.native_video_fixtures import setup as generation_setup


def native_client(transport):
    assert importlib.util.find_spec('easel.video_provider'), 'Native model transport is missing'
    from easel.video_provider import NativeVideoClient
    return NativeVideoClient(api_key='test-key', transport=httpx.MockTransport(transport))


def test_direct_catalog_keeps_credentials_inside_host(tmp_path):
    client = native_client(lambda request: pytest.fail('Discovery must not submit a request'))
    result = client.catalog()
    assert any(row['modelId'] == 'easel-ppu-h3-local::minimax-h3-dense8' for row in result)
    assert 'test-key' not in json.dumps(result)


def test_pika_uses_its_existing_private_pool_connection_and_dedicated_credential(tmp_path):
    folder = tmp_path / '.easel' / 'pika-h3-api16'
    folder.mkdir(parents=True)
    token = folder / 'token'
    token.write_text('dedicated-pool-test-key')
    token.chmod(0o600)
    connection = folder / 'connection.json'
    connection.write_text(json.dumps({'base_url': 'http://127.0.0.1:18882', 'token_file': str(token)}))
    connection.chmod(0o600)
    from easel.video_provider import NativeVideoClient
    def provider(request):
        assert request.url.port == 18882
        assert request.headers['authorization'] == 'Bearer dedicated-pool-test-key'
        return httpx.Response(200, json={'id': 'video_' + 'a' * 32, 'status': 'queued'})
    client = NativeVideoClient(repository=tmp_path, transport=httpx.MockTransport(provider))
    assert client.status('easel-ppu-pika-h3-local::pika-h3-flex-6step', 'video_' + 'a' * 32)['status'] == 'queued'


def test_plan_saves_reusable_assets_and_stable_storyboard_before_generation(generation_setup):
    from PIL import Image
    store, service, _, outputs, project, payload, _ = generation_setup
    folder = outputs / 'film'
    folder.mkdir()
    Image.new('RGB', (32, 32), 'red').save(folder / 'mother.png')
    (folder / 'production.json').write_text(json.dumps({'version': 1, 'title': '倾听',
        **{key: payload[key] for key in ['modelId', 'aspectRatio', 'resolution']},
        'clips': [{'clipId': 'first', 'seconds': 5, 'prompt': '母亲放下手机',
                   'references': [{'kind': 'image', 'path': 'film/mother.png'}]}]}))
    result = service.run({'action': 'plan', 'workshopId': project['id'], 'manifestPath': 'film/production.json'})
    assert result['state'] == 'pending'
    current = store.get(project['id'])
    assert current['shots'][0]['prompt'] == '母亲放下手机'
    assert current['assets'][0]['name'] == 'mother'
    assert current['shots'][0]['asset_ids'] == [current['assets'][0]['id']]


def test_plan_keeps_existing_editorial_shot_and_semantic_bindings_when_adding_retakes(generation_setup):
    from PIL import Image
    store, service, backend, outputs, project, payload, _ = generation_setup
    folder = outputs / 'film'
    folder.mkdir()
    Image.new('RGB', (32, 32), 'red').save(folder / 'family.png')
    Image.new('RGB', (32, 32), 'blue').save(folder / 'window.png')
    source = folder / 'adopted.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=red:s=64x112:r=10:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)],
        check=True, capture_output=True)
    project = store.production(project['id'], project['revision'], {
        'raw_content': '人工原稿', 'script': '人工已定稿的剧情',
        'assets': [
            {'id': 'mother', 'kind': 'character', 'name': '母亲', 'path': 'film/family.png',
             'image_prompt': '母亲米色毛衣', 'appearance': '披发', 'styling': '日常家居'},
            {'id': 'child', 'kind': 'character', 'name': '孩子', 'path': 'film/family.png'},
            {'id': 'blocks', 'kind': 'prop', 'name': '积木', 'path': 'film/family.png'},
        ],
        'shots': [{'id': 'scene-one', 'title': '保留作品', 'description': '人工写好的画面描述',
                   'atmosphere': '傍晚柔光', 'prompt': '人工已采用的主镜头提示词',
                   'seconds': 7, 'asset_ids': ['mother', 'child']}],
    })
    project = store.add_candidate(project['id'], project['revision'], 'scene-one', 'film/adopted.mp4',
                                  candidate_id='a' * 32, task_id='original-task')
    before_shot, before_assets = project['shots'][0], project['assets']
    (folder / 'production.json').write_text(json.dumps({'version': 1, 'title': '定向返修',
        **{key: payload[key] for key in ['modelId', 'aspectRatio', 'resolution']},
        'clips': [
            {'clipId': 'main', 'shotId': 'scene-one', 'seconds': 5, 'prompt': '本次主候选',
             'references': [{'kind': 'image', 'path': 'film/family.png'}]},
            {'clipId': 'retake', 'shotId': 'scene-one', 'seconds': 6, 'prompt': '仅失败后使用的备用',
             'references': [{'kind': 'image', 'path': 'film/window.png'}]},
        ]}))
    request = {'action': 'plan', 'workshopId': project['id'], 'manifestPath': 'film/production.json'}
    result = service.run(request)
    current = store.get(project['id'])
    assert [shot['id'] for shot in current['shots']] == ['scene-one']
    shot = current['shots'][0]
    assert {key: value for key, value in shot.items() if key != 'asset_ids'} == {
        key: value for key, value in before_shot.items() if key != 'asset_ids'}
    assert current['assets'][:3] == before_assets
    assert [asset['name'] for asset in current['assets'] if asset['id'] in shot['asset_ids']] == ['母亲', '孩子', 'window']
    assert current['raw_content'] == '人工原稿' and current['script'] == '人工已定稿的剧情'
    assert backend.submitted == []
    assert service.run(request)['planId'] == result['planId']
    assert store.get(project['id'])['revision'] == current['revision']


def test_plan_uses_first_candidate_for_new_shot_and_keeps_shared_character_reference_roles(generation_setup):
    from PIL import Image
    store, service, _, outputs, project, payload, _ = generation_setup
    folder = outputs / 'film'
    folder.mkdir()
    Image.new('RGB', (32, 32), 'red').save(folder / 'family.png')
    project = store.production(project['id'], project['revision'], {'script': '母子对话', 'shots': [],
        'assets': [{'id': 'mother', 'kind': 'character', 'name': '母亲', 'path': 'film/family.png'},
                   {'id': 'child', 'kind': 'character', 'name': '孩子', 'path': 'film/family.png'}]})
    (folder / 'production.json').write_text(json.dumps({'version': 1, 'title': '母子对话',
        **{key: payload[key] for key in ['modelId', 'aspectRatio', 'resolution']},
        'clips': [
            {'clipId': 'main', 'shotId': 'scene-one', 'seconds': 5, 'prompt': '主候选提示词',
             'references': [{'kind': 'image', 'path': 'film/family.png'}]},
            {'clipId': 'backup', 'shotId': 'scene-one', 'seconds': 6, 'prompt': '仅失败后使用的备用提示词',
             'references': [{'kind': 'image', 'path': 'film/family.png'}]},
        ]}))
    service.run({'action': 'plan', 'workshopId': project['id'], 'manifestPath': 'film/production.json'})
    current = store.get(project['id'])
    assert len(current['shots']) == 1
    shot = current['shots'][0]
    assert shot['prompt'] == '主候选提示词' and shot['seconds'] == 5
    assert shot['asset_ids'] == ['mother', 'child']
    assert len(current['assets']) == 2


def test_rework_prepares_a_new_candidate_for_the_original_stable_shot(generation_setup):
    store, service, _, _, project, payload, _ = generation_setup
    service.run(payload)
    result = service.run({**payload, 'clipId': 'first-v2', 'shotId': 'first', 'prompt': '只改变母亲的停顿'})
    assert result['shotId'] == 'first'
    assert [row['id'] for row in store.get(project['id'])['shots']] == ['first']


def test_manual_shot_edit_after_preparation_blocks_an_old_generation(generation_setup):
    store, service, backend, _, project, payload, _ = generation_setup
    service.run(payload)
    current = store.get(project['id'])
    store.production(project['id'], current['revision'], {'script': '人工修改后的剧本', 'assets': [],
        'shots': [{'id': 'first', 'title': '人工修改', 'prompt': '保留我手动修改的镜头', 'seconds': 5, 'asset_ids': []}]})
    from easel.video_workshop import WorkshopError
    with pytest.raises(WorkshopError):
        service.run({'action': 'submit', 'workshopId': project['id'], 'clipId': 'first'})
    assert backend.submitted == []


@pytest.mark.parametrize('lose_response', [False, True])
@pytest.mark.parametrize('edit_before_collect', [False, True])
def test_generation_uses_original_request_once_and_collects_into_native_workshop(tmp_path, lose_response, edit_before_collect):
    source = tmp_path / 'result.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=red:s=64x112:r=10:d=1', '-f', 'lavfi', '-i', 'sine=duration=1',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source)],
        check=True, capture_output=True)
    submissions = []
    def transport(request):
        assert request.headers['authorization'] == 'Bearer test-key'
        if request.method == 'POST':
            payload = json.loads(request.content)
            assert set(payload) == {'model', 'prompt', 'seconds', 'size', 'request_id'}
            submissions.append(payload)
            if lose_response:
                raise httpx.ReadError('lost after admission')
            return httpx.Response(200, json={'id': 'video_' + '1' * 32, 'status': 'queued'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=source.read_bytes())
        return httpx.Response(200, json={'id': 'video_' + '1' * 32, 'status': 'completed'})
    client = native_client(transport)
    outputs = tmp_path / 'workspace' / 'outputs'
    store = WorkshopStore(outputs)
    project = store.create({'title': '原生链路', 'aspect_ratio': '9:16'})
    service = VideoGenerationService(outputs=outputs, state_directory=tmp_path / 'host-private',
        client=client, actor_id='owner', ip='default', session_id='chat', authorize=lambda: None)
    payload = {'action': 'prepare', 'workshopId': project['id'], 'clipId': 'first',
        'modelId': 'easel-ppu-h3-local::minimax-h3-dense8', 'aspectRatio': '9:16',
        'seconds': 1, 'prompt': '母亲倾听孩子', 'resolution': '480p'}
    prepared = service.run(payload)
    assert prepared['status'] == 'prepared'
    payload = {key: payload[key] for key in ['workshopId', 'clipId']}
    for _ in range(2):
        result = service.run({'action': 'submit', **payload})
    assert len(submissions) == 1
    if lose_response:
        assert result['status'] == 'unconfirmed'
        assert store.get(project['id'])['deliverables'] == []
    else:
        if edit_before_collect:
            current = store.get(project['id'])
            store.production(project['id'], current['revision'], {'script': '', 'assets': [], 'shots': [{
                'id': 'first', 'title': '人工修改', 'prompt': '保留人工修改', 'seconds': 1, 'asset_ids': []}]})
        result = service.run({'action': 'collect', **payload})
        current = store.get(project['id'])
        assert result['status'] == 'collected'
        assert len(current['shots'][0]['candidates']) == 1
        assert current['shots'][0]['candidates'][0]['prompt'] == '母亲倾听孩子'
        assert len(current['deliverables']) == (0 if edit_before_collect else 1)
        if edit_before_collect:
            assert current['shots'][0]['selected_candidate_id'] is None
        service.run({'action': 'collect', **payload})
        assert len(store.get(project['id'])['deliverables']) == (0 if edit_before_collect else 1)
        assert len(submissions) == 1
