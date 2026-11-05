from pathlib import Path
from easel.host_state import publication_state_directory


def test_publication_authority_is_outside_native_workspace_and_scoped_to_repository_ip(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    first = publication_state_directory(repo, 'default')
    assert repo.resolve() not in first.parents and first != repo.resolve()
    assert first != publication_state_directory(repo, 'other-ip')
    assert first != publication_state_directory(tmp_path / 'other-repo', 'default')


def test_workspace_cannot_contain_authority_state(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    with pytest.raises(ValueError):
        publication_state_directory(tmp_path, 'default')


def test_ip_cannot_escape_host_state_directory(tmp_path):
    import pytest
    for value in ['../another', '/absolute', 'a/b', 'a\\b', '']:
        with pytest.raises(ValueError):
            publication_state_directory(tmp_path, value)


def test_generation_authority_is_separate_and_outside_native_workspace(tmp_path):
    from easel import host_state
    assert hasattr(host_state, 'generation_state_directory'), 'Missing private generation authority directory'
    repo = tmp_path / 'repo'
    repo.mkdir()
    state = host_state.generation_state_directory(repo, 'default')
    assert not state.is_relative_to(repo.resolve())
    assert state != publication_state_directory(repo, 'default')
    assert state != host_state.generation_state_directory(repo, 'other-ip')
