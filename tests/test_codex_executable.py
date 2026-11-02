"""The selected Codex executable must be shared by catalog and real turns."""
import asyncio
import os
import sys
from pathlib import Path

import pytest

from easel.harnesses.base import RunRequest, HarnessError
from easel.harnesses.codex import CodexAdapter
from easel.harnesses import codex, models

FIXTURE = Path(__file__).parent / 'fixtures' / 'harness_rpc.py'


def binaries(tmp_path):
    old = tmp_path / 'old-codex'
    marker = tmp_path / 'old-was-selected'
    old.write_text(f'#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("bad")\nraise SystemExit(20)\n')
    new = tmp_path / 'desktop-codex'
    new.write_text(f'#!{sys.executable}\nimport runpy,sys\nsys.argv=[{str(FIXTURE)!r},"codex"]\nrunpy.run_path({str(FIXTURE)!r},run_name="__main__")\n')
    for file in [old,new]:
        file.chmod(0o700)
    return old,new,marker


def request(tmp_path, executable):
    return RunRequest('hello', tmp_path, {**os.environ,
        'EASEL_CODEX_EXECUTABLE': str(executable),
        'FAKE_HARNESS_AUDIT':str(tmp_path/'audit.jsonl')}, '', '', 'read-only', 5)


def test_turn_uses_configured_codex_instead_of_an_incompatible_path_binary(tmp_path, monkeypatch):
    old,new,marker=binaries(tmp_path)
    monkeypatch.setattr(codex,'base_command',lambda _: [str(old)])
    tokens=[]
    async def reject(_):
        raise AssertionError('No tools expected')
    asyncio.run(CodexAdapter().run(request(tmp_path,new),lambda kind,text: tokens.append(text) if kind=='token' else None,reject,lambda _:None))
    assert ''.join(tokens)=='你好'
    assert not marker.exists()


def test_model_catalog_uses_the_same_configured_codex_binary(tmp_path, monkeypatch):
    old,new,marker=binaries(tmp_path)
    monkeypatch.setattr(models,'base_command',lambda _: [str(old)])
    catalog=asyncio.run(models.available_models('codex',request(tmp_path,new)))
    assert [item['id'] for item in catalog]==['first-model','second-model']
    assert not marker.exists()


def test_missing_override_never_silently_starts_the_old_path_binary(tmp_path, monkeypatch):
    old,new,marker=binaries(tmp_path)
    monkeypatch.setattr(models,'base_command',lambda _: [str(old)])
    with pytest.raises(HarnessError,match='EASEL_CODEX_EXECUTABLE'):
        asyncio.run(models.available_models('codex',request(tmp_path,tmp_path/'missing-codex')))
    assert not marker.exists()
