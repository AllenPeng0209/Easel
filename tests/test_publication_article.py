"""MP articles keep existing Markdown layout while pinning every embedded image."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import importlib
import importlib.util
from pathlib import Path
from urllib.parse import unquote

import pytest

from easel.chat_artifacts import ArtifactError, ChatArtifactStore
from easel.publication_media import capture_publication_media, stage_publication_media


@pytest.fixture
def article_module():
    try:
        return importlib.import_module('easel.publication_article')
    except ModuleNotFoundError as error:
        if error.name == 'easel.publication_article':
            pytest.fail('Snapshot-bound publication articles are not implemented')
        raise


@pytest.fixture
def article_environment(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = root / 'skills/builtin/skill-wechat-publisher/scripts/html_converter.py'
    spec = importlib.util.spec_from_file_location('publication_article_test_converter', path)
    converter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(converter)
    workspace = tmp_path / 'workspace'
    outputs = workspace / 'outputs'
    (outputs / 'album').mkdir(parents=True)
    (outputs / 'album/cover.png').write_bytes(b'APPROVED_COVER')
    (outputs / 'album/body.png').write_bytes(b'APPROVED_BODY')
    store = ChatArtifactStore(tmp_path / 'private-snapshots', outputs, workspace, 'default')
    media = capture_publication_media(store, paths=['album/cover.png', 'album/body.png'],
        session_id='article', turn_id='prepare', call_id='capture')
    return store, media, converter.convert_markdown_to_wechat_html


def image_sources(html):
    class Images(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.sources = []
        def handle_starttag(self, tag, attrs):
            if tag == 'img':
                self.sources.append(dict(attrs)['src'])
    parser = Images()
    parser.feed(html)
    return parser.sources


@pytest.mark.parametrize('coordinate', ['relative', 'outputs', 'absolute'])
def test_real_converter_layout_is_retained_and_selected_images_are_pinned(article_module, article_environment, coordinate):
    store, media, converter = article_environment
    source = {'relative':'album/cover.png', 'outputs':'outputs/album/cover.png',
              'absolute':str(store.outputs / 'album/cover.png')}[coordinate]
    article = article_module.prepare_publication_article('标题',
        f'## 分节\n\n**重点**\n\n- 列表\n\n![正文图]({source})\n\n[公开链接](https://public.example/article)',
        media, store.outputs, converter)
    assert article['sha256'] == hashlib.sha256(article['html'].encode()).hexdigest()
    assert '<h2 ' in article['html'] and '<strong ' in article['html'] and '<ul ' in article['html']
    assert 'https://public.example/article' in article['html']
    assert image_sources(article['html']) == ['easel-artifact:' + item['artifactId'] for item in media]


def test_extra_body_images_are_appended_once_but_cover_is_not_invented_in_body(article_module, article_environment):
    store, media, converter = article_environment
    text_only = article_module.prepare_publication_article('Title', 'Body', media, store.outputs, converter)
    assert image_sources(text_only['html']) == ['easel-artifact:' + media[1]['artifactId']]
    inline = article_module.prepare_publication_article('Title', '![Already used](album/body.png)',
        media, store.outputs, converter)
    assert image_sources(inline['html']) == ['easel-artifact:' + media[1]['artifactId']]


def test_article_materialization_reads_only_private_staged_snapshots_after_source_deletion(article_module, article_environment, tmp_path):
    store, media, converter = article_environment
    for item in media:
        (store.outputs / item['path']).unlink()
    article = article_module.prepare_publication_article('Title', '![Body](outputs/album/cover.png)',
        media, store.outputs, converter)
    staged = stage_publication_media(store, media, tmp_path / 'private%20task')
    html = article_module.materialize_publication_article(article, media, staged)
    paths = [Path(unquote(src)) for src in image_sources(html)]
    assert [path.read_bytes() for path in paths] == [b'APPROVED_COVER', b'APPROVED_BODY']
    assert paths == [Path(path) for path in staged]
    assert 'easel-artifact:' not in html


@pytest.mark.parametrize('source', [
    '../other.png', '/private/other-ip/outputs/_login/qr.png', '_login/qr.png',
    'outputs/_login/qr.png', 'https://remote.example/unreviewed.png',
    'data:image/png;base64,unreviewed', 'file:///private/other.png',
    '/api/media/album/cover.png', 'javascript:unreviewed', 'easel-artifact:' + 'f' * 32,
])
def test_unselected_private_remote_and_dynamic_image_sources_are_rejected(article_module, article_environment, source):
    store, media, converter = article_environment
    with pytest.raises(ArtifactError):
        article_module.prepare_publication_article('Title', f'![bad]({source})', media, store.outputs, converter)


@pytest.mark.parametrize('markup', [
    '<img src="album/cover.png" srcset="https://remote.example/2x.png 2x">',
    '<svg><image href="https://remote.example/image.png"/></svg>',
    '<img src="album/cover.png" onerror="fetch(\'/private\')">',
    '<p style="background-image:url(https://remote.example/a.png)">x</p>',
    '<p style="background:u/**/rl(https://remote.example/a.png)">x</p>',
    '<p style="background:u\\72l(https://remote.example/a.png)">x</p>',
    '<p style="background:image-set(\'https://remote.example/a.png\')">x</p>',
    '<script src="https://remote.example/a.js"></script>',
    '<iframe src="https://remote.example/"></iframe>',
    '<object data="/private/other.png"></object>',
    '<style>p{background:url(https://remote.example/a.png)}</style>',
    '<!-- <img src="/private/other.png"> -->',
    '<p title="<img src=\'/private/other.png\'>">x</p>',
])
def test_converter_html_cannot_bypass_image_pinning_through_table_html(article_module, article_environment, markup):
    store, media, converter = article_environment
    # The existing converter deliberately retains raw HTML in table cells. Test
    # its real output rather than a new renderer or a synthetic HTML-only path.
    body = '| heading |\n| --- |\n| ' + markup + ' |'
    with pytest.raises(ArtifactError):
        article_module.prepare_publication_article('Title', body, media, store.outputs, converter)


def test_materialization_rejects_corrupt_html_and_new_resource_bypasses(article_module, article_environment, tmp_path):
    store, media, converter = article_environment
    article = article_module.prepare_publication_article('Title', 'Body', media, store.outputs, converter)
    staged = stage_publication_media(store, media, tmp_path / 'task')
    with pytest.raises(ArtifactError):
        article_module.materialize_publication_article({**article, 'html':article['html'] + 'changed'}, media, staged)
    for fragment in ['<img src="/private/other.png">', '<img src="easel-artifact:' + 'f' * 32 + '">',
                     '<svg/>', '<p style="background:url(https://remote.example/x)">x</p>']:
        changed = article['html'] + fragment
        with pytest.raises(ArtifactError):
            article_module.materialize_publication_article({'html':changed,
                'sha256':hashlib.sha256(changed.encode()).hexdigest()}, media, staged)


def test_materialization_rechecks_selected_order_and_private_file_bytes(article_module, article_environment, tmp_path):
    store, media, converter = article_environment
    article = article_module.prepare_publication_article('Title', 'Body', media, store.outputs, converter)
    staged = stage_publication_media(store, media, tmp_path / 'task')
    for wrong in [staged[:1], list(reversed(staged)), [str(store.outputs / item['path']) for item in media]]:
        with pytest.raises(ArtifactError):
            article_module.materialize_publication_article(article, media, wrong)
    Path(staged[1]).write_bytes(b'UNAPPROVED_REPLACEMENT')
    with pytest.raises(ArtifactError):
        article_module.materialize_publication_article(article, media, staged)


def test_materialization_does_not_follow_staged_file_links(article_module, article_environment, tmp_path):
    store, media, converter = article_environment
    article = article_module.prepare_publication_article('Title', 'Body', media, store.outputs, converter)
    staged = stage_publication_media(store, media, tmp_path / 'task')
    secret = tmp_path / 'foreign.png'
    secret.write_bytes(b'private image')
    Path(staged[1]).unlink()
    Path(staged[1]).symlink_to(secret)
    with pytest.raises(ArtifactError):
        article_module.materialize_publication_article(article, media, staged)
