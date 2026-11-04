"""D02 an explicit file delivery can be downloaded independently of preview support."""
import io
import json
import mimetypes
import zipfile
from urllib.parse import quote

import pytest

from tests.test_chat_artifacts import artifact_http_environment
import web.app as web

pytestmark = pytest.mark.access_integration


def docx_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('[Content_Types].xml',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Delivered draft</w:t></w:r></w:p></w:body></w:document>')
    return buffer.getvalue()


def test_generic_present_receipts_download_fixed_bytes_per_file_and_scope(artifact_http_environment, monkeypatch):
    root, owner, members, anonymous, _ = artifact_http_environment
    output = root / 'outputs' / 'topic'
    originals = {
        'opaque.easel-unknown-fixture': bytes([0, 255, 128, 17, 10]),
        '字幕.srt': '1\n00:00:00,000 --> 00:00:01,000\n开场\n'.encode(),
        'script.json': b'{"scenes":[{"title":"opening"}]}\n',
        'draft.docx': docx_bytes(),
    }
    for name, content in originals.items():
        (output / name).write_bytes(content)

    monkeypatch.setattr(web, '_chat_backend', lambda _: 'codex')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'read-only'})

    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('generic-deliverable-native')
            await request.present([{'path': f'outputs/topic/{name}'} for name in originals], 'present-generic')
            emit('token', '文件已交付。')

    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    response = owner.post('/api/chat/stream', json={
        'message': '交付这四个文件', 'sessionId': 'generic-artifact', 'turnId': 'present-generic-turn',
    })
    assert response.status_code == 200, response.text
    [event] = [json.loads(line[6:]) for line in response.text.splitlines()
               if line.startswith('data: {') and '"kind": "artifact"' in line]
    artifacts = event['artifacts']
    assert [item['name'] for item in artifacts] == list(originals)
    assert len({item['url'] for item in artifacts}) == len(originals)

    for name in originals:
        (output / name).write_bytes(b'mutable replacement')
    (output / 'draft.docx').unlink()
    other_ip = owner.post('/api/projects', json={'name': 'Other delivery IP'}).json()['id']

    for artifact in artifacts:
        name, url = artifact['name'], artifact['url'] + '?download=1'
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        assert artifact['storageMode'] == 'snapshot'
        assert artifact['mime'] == mime and artifact['size'] == len(originals[name])
        download = owner.get(url)
        assert download.status_code == 200 and download.content == originals[name]
        assert download.headers['content-type'].split(';', 1)[0] == mime
        assert download.headers['content-disposition'] == f"attachment; filename*=UTF-8''{quote(name, safe='')}"
        assert download.headers['content-length'] == str(len(originals[name]))
        assert members['operator'].get(url).status_code == 404
        assert members['viewer'].get(url).status_code == 403
        assert anonymous().get(url).status_code == 401
        assert owner.get(f'/api/projects/{other_ip}' + url.removeprefix('/api')).status_code == 404
