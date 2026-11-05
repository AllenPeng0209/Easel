"""Operator-only CLI runner. Device ownership is checked before loading a model."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from .validation import SOURCE_COMMIT, read_regular, request_fingerprint, safe_path, sha256_file

MIN_FREE_BYTES = 84 * 2**30


def validate_runner(root, expected_sha):
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("A pinned runner SHA256 is required")
    actual = sha256_file(root / "run.sh")
    if actual != expected_sha:
        raise ValueError("Trusted runner script changed")
    return actual


@contextmanager
def device_leases(device, *, lock_root=Path("/tmp")):
    if type(device) is not int or not 0 <= device < 256:
        raise ValueError("Explicit physical device index required")
    fds = []
    try:
        for name in (f"easel-h3-ppu-{device}.lock", f"pika-h3-0711-ppu-{device}.lock"):
            path = safe_path(lock_root / name)
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fds.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise ValueError("Unsafe device lease file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield tuple(fds)
    finally:
        for fd in reversed(fds):
            os.close(fd)


def validate_source(root, manifest_path):
    """Manifest comes from the operator's pinned source export, never an API input."""
    manifest = json.loads(read_regular(manifest_path, 1024 * 1024))
    commit = manifest.get("source_commit", manifest.get("commit"))
    files = manifest.get("files")
    if isinstance(files, list):
        if not all(isinstance(item, dict) and isinstance(item.get("path"), str) and
                   isinstance(item.get("sha256"), str) for item in files):
            raise ValueError("Invalid source manifest entries")
        names = [item["path"] for item in files]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate source manifest entries")
        files = {item["path"]: item["sha256"] for item in files}
    if commit != SOURCE_COMMIT or not isinstance(files, dict):
        raise ValueError("Pinned official source manifest required")
    if "src/pika_h3/pipeline.py" not in files or "src/pika_h3/cli.py" not in files:
        raise ValueError("Incomplete source manifest")
    for relative, expected in files.items():
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.parts:
            raise ValueError("Unsafe source manifest path")
        if sha256_file(root / "code" / relative_path) != expected:
            raise ValueError("Pinned source file changed")
    actual = {str(path.relative_to(root / "code")) for path in (root / "code").rglob("*.py")}
    if not actual.issubset(files):
        raise ValueError("Unverified Python source in runtime")
    return {"source_commit": SOURCE_COMMIT, "manifest_sha256": sha256_file(manifest_path)}


class CliExecutor:
    def __init__(self, root, device, source_manifest, runner_sha256):
        self.root = safe_path(Path(root))
        self.device = device
        if type(device) is not int or not 0 <= device < 256:
            raise ValueError("Explicit physical device required")
        self.source_manifest = safe_path(Path(source_manifest), regular=True)
        self.source = validate_source(self.root, self.source_manifest)
        self.runner_sha256 = validate_runner(self.root, runner_sha256)
        for path in (self.root / "run.sh", self.root / "venv/bin/python"):
            # A venv interpreter is normally a symlink, so verify the interpreter's
            # resolved file separately; CLI/user output paths remain symlink-free.
            if path.name == "python":
                if not path.resolve(strict=True).is_file():
                    raise ValueError("Missing runtime interpreter")
            else:
                safe_path(path, regular=True)

    def _environment(self):
        env = dict(os.environ)
        for key in list(env):
            if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
                env.pop(key)
        env.update(H3_DEVICE=str(self.device), CUDA_VISIBLE_DEVICES=str(self.device),
                   HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                   PYTHONUNBUFFERED="1", OMP_NUM_THREADS="8")
        return env

    def _memory_probe(self, env, fds):
        # Source the same vendor SDK as run.sh; every token below is operator-owned.
        # bash positional args preserve paths without interpolating any caller input.
        source = ("import json,torch,importlib.util,pathlib; assert torch.__version__.startswith('2.9.'); "
                  "assert pathlib.Path(importlib.util.find_spec('pika_h3').origin).resolve() == "
                  f"pathlib.Path({str(self.root / 'code/src/pika_h3/__init__.py')!r}).resolve(); "
                  "assert torch.cuda.is_available() and torch.cuda.device_count()==1; "
                  "print(json.dumps({'free_bytes':torch.cuda.mem_get_info(0)[0],"
                  "'torch':torch.__version__,'device_count':torch.cuda.device_count()}))")
        command = ["bash", "-c", 'source /usr/local/PPU_SDK/envsetup.sh cuda >/dev/null; exec "$1" -c "$2"',
                   "pika-memory-probe", str(self.root / "venv/bin/python"), source]
        result = subprocess.run(command, env=env, pass_fds=fds, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        try:
            proof = json.loads(result.stdout.decode().strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise ValueError("Invalid vendor memory probe") from None
        if type(proof.get("free_bytes")) is not int or proof["free_bytes"] < MIN_FREE_BYTES:
            raise RuntimeError("Device has less than 84 GiB free; inference was not launched")
        return proof

    def __call__(self, job_id, request, directory, record_pid):
        directory = safe_path(directory)
        output = directory / "output.mp4"
        if output.exists() or output.with_suffix(".json").exists():
            raise RuntimeError("Original output already exists; refusing another launch")
        # Repeat source validation immediately before each launch to detect drift.
        source = validate_source(self.root, self.source_manifest)
        runner_sha = validate_runner(self.root, self.runner_sha256)
        command = ["bash", str(self.root / "run.sh"), request["task"], "--prompt-file",
                   str(safe_path(directory / "prompt.txt", regular=True)),
                   "--resolution", request["resolution"], "--aspect", request["size"],
                   "--duration", str(request["seconds"]), "--seed", str(request["seed"]),
                   "--output", str(output)]
        if request["task"] == "i2va":
            image = directory / ("first.png" if request["image_mime"] == "image/png" else "first.jpg")
            if sha256_file(image) != request["image_sha256"]:
                raise ValueError("Stored reference image changed")
            command.extend(["--first-image", str(image)])
        env = self._environment()
        with device_leases(self.device) as fds:
            memory = self._memory_probe(env, fds)
            audit = {**source, "runner_sha256": runner_sha, "schema": 1, "job_id": job_id,
                     "request_sha256": request_fingerprint(request),
                     "device": self.device, "load_mode": "fresh_process_nonresident",
                     "admission_free_bytes": memory["free_bytes"]}
            # Persist source/admission evidence before starting the unique child.
            from .gateway import private_write
            private_write(directory / "execution.json", json.dumps(audit).encode())
            fd = os.open(directory / "worker.log", os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as log:
                start = time.monotonic()
                process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           pass_fds=fds, start_new_session=True)
                # Linux starttime distinguishes a reused PID, and is audit only.
                identity = None
                try:
                    identity = Path(f"/proc/{process.pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
                except OSError:
                    pass
                try:
                    record_pid(process.pid, identity)
                finally:
                    code = process.wait()  # No timeout/retry/kill; shutdown drains this call.
                wall = time.monotonic() - start
                if code:
                    raise RuntimeError("Official Pika CLI exited unsuccessfully")
        return {**audit, "pid": process.pid,
                "process_start_identity": identity, "wall_seconds": wall,
                "load_mode": "fresh_process_nonresident", "admission_free_bytes": memory["free_bytes"]}
