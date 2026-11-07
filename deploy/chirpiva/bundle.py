#!/usr/bin/env python3
"""Package present, tracked source bytes; never package production state."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile


DEPLOY_FILES = {
    "bundle.py", "README.md", "install-dependencies.sh", "environment.example",
    "chirpiva.service", "Caddyfile",
}
EXTRA_FILES = {f"deploy/chirpiva/{name}" for name in DEPLOY_FILES} | {
    "tests/test_chirpiva_deployment.py", "assets/branding/xiangya-wordmark.png",
}
PRIVATE_PARTS = {
    ".git", ".easel", ".ssh", ".aws", ".aliyun", ".openclaw", ".codex",
    "projects", "outputs", "host-state", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".cache", "dist", "logs", ".next", ".nuxt", ".output",
}
PRIVATE_FILES = {
    "cookies.json", "credentials.json", "token.txt", "tokens.json", "secrets.json",
    ".git-credentials", ".npmrc", ".pypirc", ".netrc", ".runtime.json",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
}
PRIVATE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".sqlite3", ".sqlite", ".db"}
PRIVATE_KEY = re.compile(rb"(?m)^-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----\r?$")


def private_path(name: str) -> bool:
    path = PurePosixPath(name)
    parts = path.parts
    lower = [part.lower() for part in parts]
    if any(part in PRIVATE_PARTS or part.startswith(".env") for part in lower):
        return True
    if path.name.lower() in PRIVATE_FILES or path.suffix.lower() in PRIVATE_SUFFIXES:
        return True
    if parts[:1] == ("profiles",) and parts[1:2] != ("_template",):
        return True
    if parts[:1] == ("assets",):
        return not (parts[1:2] == ("readme",) or name in {
            "assets/icon.png", "assets/icon-transparent.png", "assets/icon-noword.png",
            "assets/branding/xiangya-wordmark.png", "assets/.gitkeep",
        })
    return False


def safe_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or ".." in relative.parts or "\\" in name
            or any(ord(character) < 32 for character in name)):
        raise ValueError("Unsafe source path")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Refusing source symlink: {name}")
    if not current.resolve().is_relative_to(root):
        raise ValueError("Source escaped repository")
    return current


def build(root: Path, output: Path) -> dict:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository must be a directory")
    raw = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                         check=True, capture_output=True).stdout
    tracked = set(raw.decode("utf-8").split("\0")) - {""}
    candidates = tracked | {name for name in EXTRA_FILES if (root / name).exists()}
    output = output.absolute()
    report_path = output.with_suffix(output.suffix + ".manifest.json")
    if output.exists() or output.is_symlink() or report_path.exists() or report_path.is_symlink():
        raise ValueError("Archive or manifest already exists; choose a new output name")
    if output.resolve() in {root / name for name in candidates}:
        raise ValueError("Output would overwrite a source file")
    selected, missing, excluded = [], [], 0
    for name in sorted(candidates):
        if private_path(name):
            excluded += 1
            continue
        path = safe_path(root, name)
        if not path.exists():
            if name in tracked:
                missing.append(name)
            continue
        if not path.is_file():
            raise ValueError(f"Source is not a regular file: {name}")
        before = path.stat()
        with path.open("rb") as handle:
            data = handle.read()
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError(f"Source changed while packaging: {name}; retry after edits finish")
        if PRIVATE_KEY.search(data):
            raise ValueError(f"Refusing private key material in source: {name}")
        mode = 0o755 if before.st_mode & stat.S_IXUSR else 0o644
        selected.append((name, data, mode))
    if not selected:
        raise ValueError("No source files selected")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "format": "chirpiva-source-v1",
        "policy": "present tracked source plus explicit deployment/branding allowlist; no production data",
        "missing_tracked": missing,
        "excluded_private_paths": excluded,
        "files": [{"path": name, "bytes": len(data), "mode": oct(mode),
                   "sha256": hashlib.sha256(data).hexdigest()} for name, data, mode in selected],
    }
    created_archive = False
    try:
        with output.open("xb") as stream:
            created_archive = True
            os.chmod(output, 0o600)
            with gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for name, data, mode in selected:
                        info = tarfile.TarInfo(name)
                        info.size, info.mode, info.mtime = len(data), mode, 0
                        archive.addfile(info, io.BytesIO(data))
        report["archive_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
        with report_path.open("x", encoding="utf-8") as handle:
            os.chmod(report_path, 0o600)
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except Exception:
        if created_archive:
            output.unlink(missing_ok=True)
        raise
    return {"archive": str(output), "manifest": str(report_path),
            "sha256": report["archive_sha256"], "files": len(selected),
            "bytes": output.stat().st_size, "missing_tracked": len(missing),
            "excluded_private_paths": excluded}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.repository, args.output), ensure_ascii=False, indent=2))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Bundle failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
