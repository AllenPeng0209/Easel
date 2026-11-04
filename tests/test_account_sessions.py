"""Unified account-session persistence, scope and revocation regressions."""
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib
import importlib.util
import json
import stat

import pytest


def sessions():
    assert importlib.util.find_spec("easel.account_sessions") is not None, "Unified session storage is missing"
    return importlib.import_module("easel.account_sessions")


def state(platform="douyin"):
    host = "douyin.com" if platform == "douyin" else "xiaohongshu.com"
    return {"cookies": [{"name": "session", "value": "offline-fixture-secret", "domain": "." + host,
                          "path": "/", "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"}],
            "origins": [{"origin": "https://creator." + host,
                         "localStorage": [{"name": "session-local", "value": "offline-local-secret"}]}]}


def save(module, root, *, platform="douyin", generation=None, snapshot=None):
    return module.save_session(root, platform, 7, snapshot or state(platform),
        identity={"uid": "fixture-user", "nickname": "测试账号"},
        capabilities={"read": True, "publish": "unknown"}, expected_generation=generation)


def test_session_roundtrip_is_scoped_and_returned_metadata_contains_no_browser_state(tmp_path):
    module = sessions()
    other = tmp_path / "other-ip"
    other.mkdir()
    result = save(module, tmp_path)
    assert result["account_id"] == 7
    assert "storage_state" not in result
    assert "offline-fixture-secret" not in json.dumps(result)
    record = module.load_session(tmp_path, "douyin")
    assert record["storage_state"] == state()
    assert record["identity"]["uid"] == "fixture-user"
    assert module.load_session(other, "douyin") is None
    save(module, tmp_path, platform="xhs")
    assert module.load_session(tmp_path, "xiaohongshu")["storage_state"] == state("xhs")


def test_browser_exported_unnamed_cookies_do_not_reject_an_authenticated_session(tmp_path):
    module = sessions()
    snapshot = state()
    snapshot['cookies'].append({**snapshot['cookies'][0], 'name': '', 'value': 'browser-exported-value'})
    save(module, tmp_path, snapshot=snapshot)
    restored = module.load_session(tmp_path, 'douyin')['storage_state']
    assert restored == snapshot


def test_late_login_cannot_overwrite_a_new_login_or_resurrect_a_revoked_session(tmp_path):
    module = sessions()
    old = module.begin_generation(tmp_path, "douyin")
    current = module.begin_generation(tmp_path, "douyin")
    save(module, tmp_path, generation=current)
    with pytest.raises(module.SessionConflictError):
        save(module, tmp_path, generation=old)
    assert module.load_session(tmp_path, "douyin")["generation"] == current
    module.revoke_session(tmp_path, "douyin")
    assert module.load_session(tmp_path, "douyin") is None
    with pytest.raises(module.SessionConflictError):
        save(module, tmp_path, generation=current)
    body = (tmp_path / ".easel/account-sessions/douyin.json").read_text()
    assert "offline-fixture-secret" not in body
    next_generation = module.begin_generation(tmp_path, "douyin")
    assert next_generation > current
    save(module, tmp_path, generation=next_generation)
    assert module.load_session(tmp_path, "douyin")["generation"] == next_generation


def test_generation_changes_are_serialized_between_simultaneous_callers(tmp_path):
    module = sessions()
    with ThreadPoolExecutor(max_workers=4) as workers:
        generations = list(workers.map(lambda _: module.begin_generation(tmp_path, "douyin"), range(12)))
    assert sorted(generations) == list(range(1, 13))
    save(module, tmp_path, generation=12)


def test_tombstone_generation_distinguishes_logout_from_an_unmigrated_profile(tmp_path):
    module = sessions()
    assert hasattr(module, "session_generation"), "Legacy fallback needs to recognize revoked sessions"
    assert module.session_generation(tmp_path, "douyin") == 0
    assert not (tmp_path / ".easel").exists()
    first = module.begin_generation(tmp_path, "douyin")
    assert module.load_session(tmp_path, "douyin") is None
    assert module.session_generation(tmp_path, "douyin") == first
    module.revoke_session(tmp_path, "douyin")
    assert module.session_generation(tmp_path, "douyin") > first


@pytest.mark.parametrize("mutate", [
    lambda value: value["cookies"][0].update(domain=".xiaohongshu.com"),
    lambda value: value["cookies"][0].update(domain=".douyin.com.attacker.test"),
    lambda value: value["origins"][0].update(origin="https://creator.xiaohongshu.com"),
    lambda value: value["origins"][0].update(origin="https://secret@www.douyin.com"),
    lambda value: value["origins"][0].update(localStorage="invalid"),
    lambda value: value.update(cookies=[], origins=[]),
])
def test_invalid_or_cross_platform_browser_state_is_not_persisted(tmp_path, mutate):
    module = sessions()
    snapshot = state()
    mutate(snapshot)
    with pytest.raises(module.SessionValidationError):
        save(module, tmp_path, snapshot=snapshot)
    assert module.load_session(tmp_path, "douyin") is None


def test_session_files_and_directories_are_private(tmp_path):
    module = sessions()
    save(module, tmp_path)
    directory = tmp_path / ".easel/account-sessions"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "douyin.json").stat().st_mode) == 0o600


@pytest.mark.parametrize("target", ["workspace", "easel", "directory", "record", "lock"])
def test_symlinked_session_storage_is_rejected(tmp_path, target):
    module = sessions()
    root = tmp_path / "ip"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    if target == "workspace":
        root.rmdir()
        root.symlink_to(outside, target_is_directory=True)
    elif target == "easel":
        (root / ".easel").symlink_to(outside, target_is_directory=True)
    elif target == "directory":
        (root / ".easel").mkdir()
        (root / ".easel/account-sessions").symlink_to(outside, target_is_directory=True)
    else:
        directory = root / ".easel/account-sessions"
        directory.mkdir(parents=True)
        path = directory / ("douyin.json" if target == "record" else "douyin.lock")
        path.symlink_to(outside / "external-file")
    with pytest.raises(module.SessionValidationError):
        save(module, root)
    assert list(outside.iterdir()) == []


def test_failed_atomic_replace_keeps_the_last_committed_session(tmp_path, monkeypatch):
    module = sessions()
    save(module, tmp_path)
    original = module.load_session(tmp_path, "douyin")
    replacement = copy.deepcopy(state())
    replacement["cookies"][0]["value"] = "next-fixture-secret"
    monkeypatch.setattr(module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("fixture replace failure")))
    with pytest.raises(OSError):
        save(module, tmp_path, snapshot=replacement)
    assert module.load_session(tmp_path, "douyin") == original
    assert sorted(path.name for path in (tmp_path / ".easel/account-sessions").iterdir()) == ["douyin.json", "douyin.lock"]


def test_invalid_record_and_secret_metadata_fail_without_exposing_credentials(tmp_path):
    module = sessions()
    with pytest.raises(module.SessionValidationError) as failure:
        module.save_session(tmp_path, "douyin", 7, state(), identity={"token": "sensitive-test-value"}, capabilities={})
    assert "sensitive-test-value" not in str(failure.value)
    save(module, tmp_path)
    (tmp_path / ".easel/account-sessions/douyin.json").write_text('{"storage_state":"sensitive-test-value"}')
    with pytest.raises(module.SessionValidationError) as failure:
        module.load_session(tmp_path, "douyin")
    assert "sensitive-test-value" not in str(failure.value)


def test_apply_session_uses_only_explicit_workspace_and_preserves_complete_state(tmp_path, monkeypatch):
    module = sessions()
    save(module, tmp_path)
    class Context:
        current = {"cookies": [{"name": "old-account"}], "origins": []}
        def set_storage_state(self, snapshot):
            self.current = snapshot
    context = Context()
    monkeypatch.delenv("EASEL_ROOT", raising=False)
    assert module.apply_session(context, "douyin") is False
    assert context.current["cookies"][0]["name"] == "old-account"
    monkeypatch.setenv("EASEL_ROOT", str(tmp_path))
    assert module.apply_session(context, "douyin") is True
    assert context.current == state()


def test_apply_session_requires_full_restore_support_and_masks_engine_errors(tmp_path, monkeypatch):
    module = sessions()
    save(module, tmp_path)
    monkeypatch.setenv("EASEL_ROOT", str(tmp_path))
    with pytest.raises(module.SessionApplyError):
        module.apply_session(object(), "douyin")
    class Context:
        def set_storage_state(self, _snapshot):
            raise RuntimeError("engine error containing offline-fixture-secret")
    with pytest.raises(module.SessionApplyError) as failure:
        module.apply_session(Context(), "douyin")
    assert "offline-fixture-secret" not in str(failure.value)


def test_a_revoked_unified_session_cannot_fall_back_to_an_old_browser_profile(tmp_path, monkeypatch):
    module = sessions()
    save(module, tmp_path)
    module.revoke_session(tmp_path, "douyin")
    monkeypatch.setenv("EASEL_ROOT", str(tmp_path))
    with pytest.raises(module.SessionApplyError):
        module.apply_session(object(), "douyin")
