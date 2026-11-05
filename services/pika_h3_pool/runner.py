"""Run the already deployed official Pika CLI under a scheduler-owned lease."""

from pathlib import Path
import json
import os
import subprocess
import time

from services.pika_h3_ppu.executor import CliExecutor, validate_source, validate_runner
from services.pika_h3_ppu.gateway import private_write
from services.pika_h3_ppu.validation import request_fingerprint, safe_path, sha256_file


class PoolCliRunner:
    def __init__(self, root, source_manifest, runner_sha256):
        initial = CliExecutor(root, 0, source_manifest, runner_sha256)
        self.root, self.source_manifest = initial.root, initial.source_manifest
        self.runner_sha256, self.source = initial.runner_sha256, initial.source

    def __call__(self, job_id, request, directory, record_pid, lease):
        directory = safe_path(directory)
        output = directory / "output.mp4"
        if output.exists() or output.with_suffix(".json").exists():
            raise RuntimeError("Original output exists; no second launch")
        source = validate_source(self.root, self.source_manifest)
        runner_sha = validate_runner(self.root, self.runner_sha256)
        helper = CliExecutor(
            self.root, lease.device, self.source_manifest, self.runner_sha256
        )
        command = [
            "bash",
            str(self.root / "run.sh"),
            request["task"],
            "--prompt-file",
            str(safe_path(directory / "prompt.txt", regular=True)),
            "--resolution",
            request["resolution"],
            "--aspect",
            request["size"],
            "--duration",
            str(request["seconds"]),
            "--seed",
            str(request["seed"]),
            "--cpu-offload",
            "--output",
            str(output),
        ]
        if request["task"] == "i2va":
            image = directory / (
                "first.png" if request["image_mime"] == "image/png" else "first.jpg"
            )
            if sha256_file(image) != request["image_sha256"]:
                raise ValueError("Original reference changed")
            command.extend(["--first-image", str(image)])
        env = helper._environment()
        memory = helper._memory_probe(env, lease.fds)
        audit = {
            **source,
            "runner_sha256": runner_sha,
            "schema": 1,
            "job_id": job_id,
            "request_sha256": request_fingerprint(request),
            "device": lease.device,
            "load_mode": "fresh_process_nonresident",
            "admission_free_bytes": memory["free_bytes"],
            "cpu_offload": True,
        }
        private_write(directory / "execution.json", json.dumps(audit).encode())
        fd = os.open(
            directory / "worker.log",
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "wb") as log:
            started = time.monotonic()
            child = subprocess.Popen(
                command,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                pass_fds=lease.fds,
                start_new_session=True,
            )
            identity = None
            try:
                identity = (
                    Path(f"/proc/{child.pid}/stat")
                    .read_text()
                    .rsplit(") ", 1)[1]
                    .split()[19]
                )
            except OSError:
                pass
            try:
                record_pid(child.pid, identity)
            finally:
                code = child.wait()
            wall = time.monotonic() - started
        if code:
            raise RuntimeError("Official Pika CLI exited unsuccessfully")
        return {
            **audit,
            "pid": child.pid,
            "process_start_identity": identity,
            "wall_seconds": wall,
        }
