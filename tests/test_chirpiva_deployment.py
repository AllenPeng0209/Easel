"""Release archives must preserve the working tree without exporting host data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/chirpiva/bundle.py"


def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def tracked(root, name, text="source\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    subprocess.run(["git", "-C", str(root), "add", "-f", "--", name], check=True)
    return path


def run_bundle(root, output):
    return subprocess.run([sys.executable, str(SCRIPT), "--repository", str(root),
                           "--output", str(output)], text=True, capture_output=True)


def test_packages_current_bytes_and_excludes_deleted_tracked_files(tmp_path):
    root = repository(tmp_path)
    source = tracked(root, "web/app.py", "old\n")
    source.write_text("new\n")
    tracked(root, "deleted.png").unlink()
    output = tmp_path / "source.tar.gz"
    result = run_bundle(root, output)
    assert result.returncode == 0, result.stderr
    with tarfile.open(output) as archive:
        assert archive.getnames() == ["web/app.py"]
        assert archive.extractfile("web/app.py").read() == b"new\n"
    report = json.loads(output.with_suffix(output.suffix + ".manifest.json").read_text())
    assert report["files"][0]["sha256"] == hashlib.sha256(b"new\n").hexdigest()
    assert report["missing_tracked"] == ["deleted.png"]
    assert report["archive_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_private_paths_are_excluded_even_if_accidentally_tracked(tmp_path):
    root = repository(tmp_path)
    tracked(root, "web/app.py")
    private = [".env", ".env.local", "nested/.env.production", ".git-credentials", ".npmrc",
               "keys/server.pem", "credentials.json", "id_ed25519", "token.txt",
               "projects/one/profile.md", "outputs/session.json", ".easel/access.sqlite3",
               "host-state/receipt.json", "node_modules/a/index.js", ".venv/bin/python",
               "profiles/owner/identity.md", "assets/private-photo.png", "cookies.json",
               "integrations/huobao-drama/.runtime.json"]
    for name in private:
        tracked(root, name, "DO_NOT_EXPORT\n")
    output = tmp_path / "source.tar.gz"
    result = run_bundle(root, output)
    assert result.returncode == 0, result.stderr
    with tarfile.open(output) as archive:
        assert archive.getnames() == ["web/app.py"]


def test_only_explicit_untracked_deployment_and_brand_files_are_added(tmp_path):
    root = repository(tmp_path)
    tracked(root, "web/app.py")
    for name in ["deploy/chirpiva/README.md", "assets/branding/xiangya-wordmark.png",
                 "deploy/chirpiva/private-note.txt", "untracked.py"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("example\n")
    output = tmp_path / "source.tar.gz"
    result = run_bundle(root, output)
    assert result.returncode == 0, result.stderr
    with tarfile.open(output) as archive:
        assert set(archive.getnames()) == {"web/app.py", "deploy/chirpiva/README.md",
                                           "assets/branding/xiangya-wordmark.png"}


def test_symlink_cannot_export_a_file_outside_repository(tmp_path):
    root = repository(tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("DO_NOT_EXPORT")
    (root / "readme.md").symlink_to(secret)
    subprocess.run(["git", "-C", str(root), "add", "readme.md"], check=True)
    output = tmp_path / "source.tar.gz"
    result = run_bundle(root, output)
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()
    assert not output.exists()


def test_replaced_parent_directory_symlink_is_rejected(tmp_path):
    root = repository(tmp_path)
    tracked(root, "src/module.py").unlink()
    (root / "src").rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "module.py").write_text("DO_NOT_EXPORT")
    (root / "src").symlink_to(outside, target_is_directory=True)
    result = run_bundle(root, tmp_path / "source.tar.gz")
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()


def test_existing_archive_is_not_overwritten(tmp_path):
    root = repository(tmp_path)
    tracked(root, "app.py")
    output = tmp_path / "source.tar.gz"
    output.write_bytes(b"keep-existing")
    result = run_bundle(root, output)
    assert result.returncode != 0
    assert output.read_bytes() == b"keep-existing"
    assert "exists" in result.stderr.lower()


def test_private_key_material_is_rejected_in_an_ordinary_source_file(tmp_path):
    root = repository(tmp_path)
    tracked(root, "config.txt", "-----BEGIN PRIVATE KEY-----\nreal-key-material\n")
    output = tmp_path / "source.tar.gz"
    result = run_bundle(root, output)
    assert result.returncode != 0
    assert "private key" in result.stderr.lower()
    assert not output.exists()
