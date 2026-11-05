"""D07 media comparisons pin both bytes and only corroborated generation metadata."""
import hashlib

import pytest
from PIL import Image

from easel.projects import project_context
from easel.request_identity import actor_context
from easel.video_workshop import WorkshopStore
from tests.test_artifact_version_http import declare
from tests.test_chat_artifacts import artifact_http_environment
from tests.test_video_workshop import video_bytes
import web.app as web

pytestmark = pytest.mark.access_integration


def compare(owner, before, after):
    return owner.get(f'/api/chat/artifacts/{after["artifactId"]}/compare', params={'beforeId': before['artifactId']})


def test_image_pair_contains_exact_versions_and_explicit_unknown_generation(artifact_http_environment):
    root, owner, members, _, _ = artifact_http_environment
    path = root / 'outputs/topic/cover.png'
    Image.new('RGB', (2, 2), 'red').save(path)
    before_bytes = path.read_bytes()
    before = declare(artifact_http_environment, 'outputs/topic/cover.png', 'image-before')
    Image.new('RGB', (2, 2), 'blue').save(path)
    after_bytes = path.read_bytes()
    after = declare(artifact_http_environment, 'outputs/topic/cover.png', 'image-after')
    path.unlink()
    response = compare(owner, before, after)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['kind'] == 'media' and result['path'] == 'topic/cover.png' and result['identical'] is False
    for key, metadata, content in [('before', before, before_bytes), ('after', after, after_bytes)]:
        side = result[key]
        assert side == {**{field: metadata[field] for field in ('artifactId', 'revision', 'sha256', 'size', 'mime', 'url')},
                        'generation': None}
        assert owner.get(side['url']).content == content
    assert compare(members['operator'], before, after).status_code == 404


def test_video_metadata_joins_only_exact_adopted_workshop_hash_and_render_receipt(artifact_http_environment, video_bytes):
    root, owner, _, _, _ = artifact_http_environment
    path = root / 'outputs/topic/clip.mp4'
    old_bytes = video_bytes
    path.write_bytes(old_bytes)
    before = declare(artifact_http_environment, 'outputs/topic/clip.mp4', 'video-before')
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        workshop = WorkshopStore(web._outputs_dir())
        project = workshop.create({'title': 'Recorded render'})
        project.update(revision=2, deliverables=[{
            'id': 'a' * 32, 'render_id': 'b' * 32, 'path': 'topic/clip.mp4',
            'sha256': hashlib.sha256(old_bytes).hexdigest(), 'revision': 2,
        }], renders=[{
            'id': 'b' * 32, 'task_id': 'c' * 32, 'artifact_id': 'a' * 32, 'collected_revision': 2,
            'content_revision': 1, 'status': 'succeeded',
            'submission_state': 'confirmed',
            'unverifiedPrompt': 'should never be exported',
        }])
        workshop._write(project)
    path.write_bytes(old_bytes + b'\0\0\0\x08free')
    after = declare(artifact_http_environment, 'outputs/topic/clip.mp4', 'video-after')
    response = compare(owner, before, after)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['before']['generation'] == {
        'source': 'native_workshop', 'workshopProjectId': project['id'], 'renderId': 'b' * 32,
        'taskId': 'c' * 32, 'contentRevision': 1,
    }
    assert result['after']['generation'] is None
    assert 'unverifiedPrompt' not in response.text


@pytest.mark.parametrize('source,status', [
    (b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>', 200),
    ('<!DOCTYPE svg [<!ENTITY placeholder "text">]><svg>&placeholder;</svg>'.encode('utf-16'), 415),
])
def test_svg_comparison_rejects_unsafe_or_unverified_markup(artifact_http_environment, source, status):
    root, owner, _, _, _ = artifact_http_environment
    (root / 'outputs/topic/graphic.svg').write_bytes(source)
    before = declare(artifact_http_environment, 'outputs/topic/graphic.svg', 'svg-before')
    after = declare(artifact_http_environment, 'outputs/topic/graphic.svg', 'svg-after')
    assert compare(owner, before, after).status_code == status
