"""Preparing an approval must not execute a mutable workspace script."""
import runpy
from web import app as web


def test_article_renderer_is_pinned_before_agent_requests(monkeypatch, tmp_path):
    def mutable_script(*args, **kwargs):
        raise AssertionError('approval preparation executed workspace Python again')
    monkeypatch.setattr(runpy, 'run_path', mutable_script)
    monkeypatch.setattr(web, 'WECHAT_SKILL_SCRIPTS', tmp_path)
    rendered = web._publication_article_converter('## Reviewed heading\n\nReviewed body')
    assert 'Reviewed heading' in rendered and 'Reviewed body' in rendered
