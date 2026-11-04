"""D10 exact Office snapshot preview, bounded conversion and truthful errors."""
import asyncio
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from tests.test_artifact_version_http import declare
from tests.test_chat_artifacts import artifact_http_environment

pytestmark = pytest.mark.access_integration

WORD_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
SLIDE_TYPE = 'application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml'
PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n'


def office_bytes(extension, *, external=False, embedded=False, encoded=False, svg=False):
    word = extension == 'docx'
    main = 'word/document.xml' if word else 'ppt/presentation.xml'
    content_type = WORD_TYPE if word else SLIDE_TYPE
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('[Content_Types].xml',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Override PartName="/{main}" ContentType="{content_type}"/></Types>')
        archive.writestr(main,
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:rFonts w:ascii="Present Font" w:eastAsia="不存在的测试字体"/></w:document>' if word else
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
        if external or encoded:
            target = ('Target="https://external.invalid/track.png" TargetMode="External"' if external else
                      'Target="%2Fetc%2Fpasswd"')
            archive.writestr('word/_rels/document.xml.rels',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                f'{target}/></Relationships>')
        if embedded:
            archive.writestr('word/embeddings/object.bin', b'embedded execution data')
        if svg:
            archive.writestr('word/media/unsafe.svg', '<svg xmlns="http://www.w3.org/2000/svg">'
                '<image href="https://external.invalid/image.png"/></svg>')
    return buffer.getvalue()


def preview(owner, artifact):
    return owner.get(f'/api/chat/artifacts/{artifact["artifactId"]}/office-preview')


@pytest.mark.parametrize('extension', ['docx', 'pptx'])
def test_office_preview_uses_exact_snapshot_and_isolated_process(artifact_http_environment, monkeypatch, extension):
    from easel import office_preview as office

    root, owner, members, _, _ = artifact_http_environment
    content = office_bytes(extension)
    path = root / f'outputs/topic/document.{extension}'
    path.write_bytes(content)
    artifact = declare(artifact_http_environment, f'outputs/topic/document.{extension}', 'office-preview')
    path.write_bytes(b'mutable replacement')
    calls = []

    async def process(args, **options):
        calls.append(args)
        if args[0] == '/fixture/fc-list':
            return SimpleNamespace(returncode=0, timed_out=False, output_truncated=False, stdout='Present Font\n')
        source = Path(args[-1])
        assert source.read_bytes() == content and source != path
        assert any(arg.startswith('-env:UserInstallation=file:') for arg in args)
        assert 'OPENAI_API_KEY' not in options['env']
        (Path(options['cwd']) / 'source.pdf').write_bytes(PDF)
        return SimpleNamespace(returncode=0, timed_out=False)

    monkeypatch.setattr(office, '_find_soffice', lambda: '/fixture/soffice')
    monkeypatch.setattr(office, '_find_font_inventory', lambda: '/fixture/fc-list')
    monkeypatch.setattr(office, '_run_local_process', process)
    response = preview(owner, artifact)
    assert response.status_code == 200, response.text
    assert response.content == PDF and response.headers['content-type'] == 'application/pdf'
    assert response.headers['x-easel-office-source-sha256'] == artifact['sha256']
    if extension == 'docx':
        assert response.headers['x-easel-office-font-status'] == 'checked'
        assert response.headers['x-easel-office-font-source'] == 'declared_inventory'
        assert json.loads(unquote(response.headers['x-easel-office-missing-fonts'])) == ['不存在的测试字体']
    assert preview(members['operator'], artifact).status_code == 404
    assert preview(members['viewer'], artifact).status_code == 403
    other_ip = owner.post('/api/projects', json={'name': 'Other Office IP'}).json()['id']
    assert owner.get(f'/api/projects/{other_ip}/chat/artifacts/{artifact["artifactId"]}/office-preview').status_code == 404
    assert len([call for call in calls if call[0] == '/fixture/soffice']) == 1


@pytest.mark.parametrize('unsafe', ['external', 'embedded', 'encoded', 'svg', 'malformed'])
def test_unsafe_office_never_starts_converter(artifact_http_environment, monkeypatch, unsafe):
    from easel import office_preview as office

    root, owner, _, _, _ = artifact_http_environment
    path = root / 'outputs/topic/unsafe.docx'
    path.write_bytes(office_bytes('docx', **{unsafe: True})
                     if unsafe != 'malformed' else b'not an Office document')
    artifact = declare(artifact_http_environment, 'outputs/topic/unsafe.docx', 'office-unsafe')
    monkeypatch.setattr(office, '_find_soffice', lambda: '/fixture/soffice')
    monkeypatch.setattr(office, '_run_local_process', lambda *_args, **_kwargs: pytest.fail('Unsafe input launched converter'))
    response = preview(owner, artifact)
    assert response.status_code == 422 and response.json()['detail']['code'] == 'invalid_document'


def test_office_unavailable_has_truthful_error_and_original_still_downloads(artifact_http_environment, monkeypatch):
    from easel import office_preview as office

    root, owner, _, _, _ = artifact_http_environment
    content = office_bytes('docx')
    (root / 'outputs/topic/document.docx').write_bytes(content)
    artifact = declare(artifact_http_environment, 'outputs/topic/document.docx', 'office-unavailable')
    monkeypatch.setattr(office, '_find_soffice', lambda: None)
    response = preview(owner, artifact)
    assert response.status_code == 503 and response.json()['detail']['code'] == 'converter_unavailable'
    assert owner.get(artifact['url'] + '?download=1').content == content


@pytest.mark.parametrize('case,status,code', [
    ('timeout', 504, 'conversion_timeout'), ('large_pdf', 413, 'output_too_large'),
    ('busy', 503, 'busy'), ('large_source', 413, 'input_too_large'),
])
def test_office_preview_limits_and_timeouts(artifact_http_environment, monkeypatch, case, status, code):
    from easel import office_preview as office

    root, owner, _, _, _ = artifact_http_environment
    (root / 'outputs/topic/document.docx').write_bytes(office_bytes('docx'))
    artifact = declare(artifact_http_environment, 'outputs/topic/document.docx', 'office-limit')
    monkeypatch.setattr(office, '_find_soffice', lambda: '/fixture/soffice')

    async def process(args, **options):
        if case == 'large_pdf':
            (Path(options['cwd']) / 'source.pdf').write_bytes(PDF)
        else:
            assert case == 'timeout'
        return SimpleNamespace(returncode=0, timed_out=case == 'timeout')

    monkeypatch.setattr(office, '_run_local_process', process)
    if case == 'large_pdf':
        monkeypatch.setattr(office, 'MAX_PDF_BYTES', len(PDF) - 1)
    elif case == 'large_source':
        monkeypatch.setattr(office, '_MAX_SOURCE_BYTES', 1)
    elif case == 'busy':
        assert office._SLOTS.acquire(blocking=False)
        assert office._SLOTS.acquire(blocking=False)
    try:
        response = preview(owner, artifact)
        assert response.status_code == status and response.json()['detail']['code'] == code
    finally:
        if case == 'busy':
            office._SLOTS.release()
            office._SLOTS.release()


def test_disconnected_preview_waits_for_owned_process_cleanup(tmp_path, monkeypatch):
    from easel import office_preview as office

    async def scenario():
        disconnected_calls = 0
        clean = asyncio.Event()

        async def disconnected():
            nonlocal disconnected_calls
            disconnected_calls += 1
            return disconnected_calls > 1

        async def process(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(.01)
                clean.set()

        monkeypatch.setattr(office, '_run_local_process', process)
        with pytest.raises(asyncio.CancelledError):
            await office._convert_process(['soffice'], tmp_path, tmp_path / 'source.pdf', disconnected)
        assert clean.is_set()

    asyncio.run(scenario())
