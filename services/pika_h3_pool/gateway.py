"""Persistent FIFO dispatch over an explicitly configured pool of PPU devices."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
import uuid
from urllib.parse import urlsplit

from services.pika_h3_ppu.gateway import (
    VideoService,
    make_server as single_server,
    private_write,
    read_token,
)
from services.pika_h3_ppu.validation import (
    MODEL,
    normalize,
    request_fingerprint,
    safe_path,
    validate_result,
)


class DeviceLease:
    def __init__(self, pool, device, fds):
        self.pool, self.device, self.fds = pool, device, tuple(fds)
        self.closed = False

    def close(self):
        with self.pool.lock:
            if not self.closed:
                for fd in reversed(self.fds):
                    os.close(fd)
                self.pool.active.remove(self.device)
                self.closed = True


class DevicePool:
    def __init__(
        self,
        devices,
        *,
        lock_root=Path("/tmp"),
        memory_probe=None,
        host_memory_probe=None,
    ):
        self.devices = tuple(devices)
        if (
            not self.devices
            or len(set(self.devices)) != len(self.devices)
            or any(type(d) is not int or not 0 <= d < 256 for d in self.devices)
        ):
            raise ValueError("Unique physical device indices are required")
        self.lock_root = safe_path(Path(lock_root))
        self.lock_root.mkdir(exist_ok=True, parents=True, mode=0o700)
        self.memory_probe = memory_probe or self._smi_probe
        self.host_memory_probe = host_memory_probe or (
            self._host_free_bytes if memory_probe is None else None
        )
        self.lock, self.active = threading.RLock(), set()
        self.cursor, self.last_error = 0, None

    @staticmethod
    def _host_free_bytes():
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
        raise RuntimeError("Host memory observation unavailable")

    @staticmethod
    def _smi_probe():
        # An observation failure is never interpreted as idle hardware.
        result = subprocess.run(
            [
                "/usr/local/PPU_SDK/CUDA_SDK/bin/nvidia-smi",
                "--query-gpu=index,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        free = {}
        for line in result.stdout.splitlines():
            values = [int(v.strip()) for v in line.split(",")]
            if len(values) != 3:
                raise ValueError("Unrecognized device observation")
            if values[1] <= 1024:
                free[values[0]] = values[2] * 2**20
        if not free:
            raise RuntimeError("No observed idle device")
        return free

    def acquire(self):
        with self.lock:
            try:
                free = self.memory_probe()
                if not isinstance(free, dict):
                    raise ValueError("Invalid observation")
                if self.host_memory_probe is not None:
                    available = self.host_memory_probe()
                    if type(available) is not int:
                        raise ValueError("Invalid host memory observation")
                    # Conservatively reserve CPU-offloaded weights for every
                    # admitted job before their RSS has appeared in MemAvailable.
                    if available < (128 + 120 * len(self.active)) * 2**30:
                        self.last_error = "insufficient_host_memory"
                        return None
                self.last_error = None
            except Exception:
                self.last_error = "hardware_observation_unavailable"
                return None
            for offset in range(len(self.devices)):
                index = (self.cursor + offset) % len(self.devices)
                device = self.devices[index]
                if (
                    device in self.active
                    or type(free.get(device)) is not int
                    or free[device] < 84 * 2**30
                ):
                    continue
                fds = []
                try:
                    for name in (
                        f"easel-h3-ppu-{device}.lock",
                        f"pika-h3-1104-ppu-{device}.lock",
                        f"pika-h3-0711-ppu-{device}.lock",
                    ):
                        fd = os.open(
                            safe_path(self.lock_root / name),
                            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                            0o600,
                        )
                        fds.append(fd)
                        info = os.fstat(fd)
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or info.st_uid != os.geteuid()
                        ):
                            raise ValueError("Unsafe device lease")
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, ValueError):
                    for fd in reversed(fds):
                        os.close(fd)
                    continue
                self.active.add(device)
                self.cursor = (index + 1) % len(self.devices)
                return DeviceLease(self, device, fds)
            return None


class PoolService(VideoService):
    def __init__(
        self,
        root,
        token,
        runner,
        *,
        allocator,
        max_concurrency=None,
        max_pending=128,
        start_workers=True,
        tested_modes=(),
        validator=validate_result,
    ):
        if max_concurrency is None:
            max_concurrency = min(4, len(allocator.devices))
        if (
            not isinstance(token, str)
            or len(token) < 32
            or type(max_pending) is not int
            or max_pending < 1
            or type(max_concurrency) is not int
            or not 1 <= max_concurrency <= len(allocator.devices)
        ):
            raise ValueError(
                "Private token, queue limit and valid concurrency required"
            )
        self.root = safe_path(Path(root))
        if self.root.exists() and self.root.stat().st_mode & 0o077:
            raise ValueError("Existing state root must have mode 0700")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_uid != os.geteuid():
            raise ValueError("State root must belong to the service user")
        self.lease_fd = os.open(
            safe_path(self.root / "service.lock"),
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(self.lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.lease_fd)
            raise
        self.db = sqlite3.connect(
            safe_path(self.root / "jobs.sqlite"), check_same_thread=False
        )
        (self.root / "jobs.sqlite").chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, request_id TEXT UNIQUE, fingerprint TEXT NOT NULL,
          request TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
          updated REAL NOT NULL, pid INTEGER, process_identity TEXT, receipt TEXT,
          error TEXT, device INTEGER)""")
        # Queued means the durable dispatch boundary was never crossed. Only
        # in_progress is uncertain after a crash; never launch it a second time.
        self.db.execute(
            "UPDATE jobs SET status='unknown',error='interrupted_no_automatic_retry' WHERE status='in_progress'"
        )
        self.db.commit()
        self.lock, self.stopping = threading.RLock(), threading.Event()
        self.token, self.runner, self.validator = token, runner, validator
        self.allocator = allocator
        self.max_pending, self.max_concurrency = max_pending, max_concurrency
        self.tested_modes, self.closed = list(tested_modes), False
        self.recovery_lock, self.recovery_thread = threading.Lock(), None
        self.recovery_signatures, self.workers = {}, []
        for row in self.db.execute(
            "SELECT * FROM jobs WHERE status='unknown'"
        ).fetchall():
            self._maybe_reconcile(row)
        if start_workers:
            for number in range(max_concurrency):
                thread = threading.Thread(
                    target=self._work, name=f"pika-pool-{number}", daemon=True
                )
                self.workers.append(thread)
                thread.start()

    def submit(self, payload):
        request, image = normalize(payload)
        fingerprint = request_fingerprint(request)
        with self.lock:
            prior = (
                self.db.execute(
                    "SELECT id,fingerprint FROM jobs WHERE request_id=?",
                    (request["request_id"],),
                ).fetchone()
                if request["request_id"]
                else None
            )
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise FileExistsError("request_id belongs to a different input")
                return prior["id"]
            if self.stopping.is_set():
                raise RuntimeError("Gateway is draining")
            if (
                self.db.execute(
                    "SELECT count(*) FROM jobs WHERE status='queued'"
                ).fetchone()[0]
                >= self.max_pending
            ):
                raise OverflowError("Queue is full")
            job = "video_" + uuid.uuid4().hex
            directory = self.root / job
            directory.mkdir(mode=0o700)
            private_write(directory / "prompt.txt", request["prompt"].encode())
            if image is not None:
                private_write(
                    directory
                    / (
                        "first.png"
                        if request["image_mime"] == "image/png"
                        else "first.jpg"
                    ),
                    image,
                )
            now = time.time()
            self.db.execute(
                "INSERT INTO jobs(id,request_id,fingerprint,request,status,created,updated) VALUES(?,?,?,?,?,?,?)",
                (
                    job,
                    request["request_id"],
                    fingerprint,
                    json.dumps(request, ensure_ascii=False),
                    "queued",
                    now,
                    now,
                ),
            )
            self.db.commit()
            return job

    def _work(self):
        while not self.stopping.is_set():
            with self.lock:
                queued = self.db.execute(
                    "SELECT 1 FROM jobs WHERE status='queued' LIMIT 1"
                ).fetchone()
            if not queued:
                self.stopping.wait(0.05)
                continue
            lease = self.allocator.acquire()
            if lease is None:
                self.stopping.wait(0.25)
                continue
            try:
                with self.lock:
                    if self.stopping.is_set():
                        continue
                    row = self.db.execute(
                        "SELECT rowid,* FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1"
                    ).fetchone()
                    if row is None:
                        continue
                    self.db.execute(
                        "UPDATE jobs SET status='in_progress',device=?,updated=? WHERE id=? AND status='queued'",
                        (lease.device, time.time(), row["id"]),
                    )
                    self.db.commit()
                job, request = row["id"], json.loads(row["request"])

                def record_pid(pid, identity):
                    with self.lock:
                        self.db.execute(
                            "UPDATE jobs SET pid=?,process_identity=?,updated=? WHERE id=?",
                            (pid, identity, time.time(), job),
                        )
                        self.db.commit()

                phase = "execution"
                try:
                    directory = self._directory(job)
                    audit = self.runner(job, request, directory, record_pid, lease)
                    phase = "audit_validation"
                    self._validate_audit(audit, job, request)
                    phase = "result_validation"
                    receipt = self.validator(directory / "output.mp4", request)
                    receipt["execution"] = audit
                    for name in ["output.mp4", "output.json"]:
                        safe_path(directory / name, regular=True).chmod(0o600)
                    self._update(job, "completed", receipt=receipt)
                except Exception as error:
                    try:
                        private_write(
                            self._directory(job) / "failure.json",
                            json.dumps(
                                {
                                    "schema": 1,
                                    "phase": phase,
                                    "error_type": type(error).__name__,
                                    "code": "generation_or_validation_failed_no_retry",
                                }
                            ).encode(),
                        )
                    except (OSError, ValueError, KeyError):
                        pass
                    self._update(
                        job, "failed", "generation_or_validation_failed_no_retry"
                    )
            finally:
                lease.close()

    def status(self, job_id):
        with self.lock:
            result = super().status(job_id)
            row = self.db.execute(
                "SELECT rowid,device FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if result["status"] == "queued":
                result["queue_position"] = self.db.execute(
                    "SELECT count(*) FROM jobs WHERE status='queued' AND rowid<=?",
                    (row["rowid"],),
                ).fetchone()[0]
            else:
                result["queue_position"] = None
            if row["device"] is not None:
                result["device"] = row["device"]
            return result

    def queue_status(self):
        with self.lock:
            counts = dict(
                self.db.execute(
                    "SELECT status,count(*) FROM jobs GROUP BY status"
                ).fetchall()
            )
            devices = [
                r[0]
                for r in self.db.execute(
                    "SELECT device FROM jobs WHERE status='in_progress' ORDER BY device"
                )
            ]
            return {
                "queued": counts.get("queued", 0),
                "active": counts.get("in_progress", 0),
                "completed": counts.get("completed", 0),
                "failed": counts.get("failed", 0),
                "unknown": counts.get("unknown", 0),
                "cancelled": counts.get("cancelled", 0),
                "pool_size": len(self.allocator.devices),
                "devices": list(self.allocator.devices),
                "active_devices": devices,
                "max_concurrency": self.max_concurrency,
                "max_pending": self.max_pending,
                "draining": self.stopping.is_set(),
                "resource_observation_error": self.allocator.last_error,
            }

    def cancel(self, job_id):
        with self.lock:
            status = self.status(job_id)["status"]
            if status == "cancelled":
                return self.status(job_id)
            if status != "queued":
                raise RuntimeError("Only queued jobs may be cancelled")
            self._update(job_id, "cancelled")
            return self.status(job_id)

    def reconcile(self, job_id):
        """Validate original bytes only. This action never dispatches a model."""
        with self.lock:
            result = self.status(job_id)
            if result["status"] == "completed":
                return result
            if result["status"] not in ["failed", "unknown"]:
                raise RuntimeError("Only terminal original output may be revalidated")
            self.recovery_signatures.pop(job_id, None)
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            self._maybe_reconcile(row)
            result = self.status(job_id)
            result["revalidation_enqueued"] = job_id in self.recovery_signatures
            return result

    def begin_drain(self):
        with self.lock:
            self.stopping.set()

    def close(self):
        self.begin_drain()
        for thread in self.workers:
            thread.join()
        if self.recovery_thread:
            self.recovery_thread.join()
        with self.lock:
            if not self.closed:
                self.db.close()
                os.close(self.lease_fd)
                self.closed = True


def make_server(service, host="127.0.0.1", port=8882):
    server = single_server(service, host, port)
    original = server.RequestHandlerClass

    class Handler(original):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path not in ["/health", "/v1/queue"]:
                return super().do_GET()
            if not self._auth():
                return
            data = service.queue_status()
            if path == "/health":
                data.update(
                    status="draining" if service.stopping.is_set() else "ok",
                    model=MODEL,
                    resident=False,
                    model_ready="not_probed",
                    tested_modes=service.tested_modes,
                    queue_policy="durable_fifo",
                )
            self._json(200, data)

        def do_POST(self):
            match = re.fullmatch(
                r"/v1/videos/(video_[a-f0-9]{32})/(cancel|reconcile)", self.path
            )
            if not match:
                return super().do_POST()
            if not self._auth():
                return
            try:
                if (
                    self.headers.get("Transfer-Encoding")
                    or self.headers.get_content_type() != "application/json"
                ):
                    raise ValueError("Expected JSON")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 or json.loads(self.rfile.read(length)) != {}:
                    raise ValueError("Expected empty JSON object")
                action = service.cancel if match[2] == "cancel" else service.reconcile
                self._json(200, action(match[1]))
            except KeyError:
                self._json(404, {"error": {"code": "not_found"}})
            except RuntimeError:
                self._json(409, {"error": {"code": "already_dispatched"}})
            except (ValueError, UnicodeError):
                self._json(400, {"error": {"code": "invalid_request"}})

    server.RequestHandlerClass = Handler
    return server


def verify_media_tools():
    missing = [name for name in ["ffmpeg", "ffprobe"] if shutil.which(name) is None]
    if missing:
        raise RuntimeError("Media tools unavailable: " + ", ".join(missing))
    versions = {}
    for name in ["ffmpeg", "ffprobe"]:
        result = subprocess.run(
            [name, "-version"], check=True, capture_output=True, text=True, timeout=10
        )
        versions[name] = result.stdout.splitlines()[0]
    return versions


def main():
    from .runner import PoolCliRunner

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runtime-root", type=Path, required=True)
    p.add_argument("--state-root", type=Path, required=True)
    p.add_argument("--token-file", type=Path, required=True)
    p.add_argument("--source-manifest", type=Path, required=True)
    p.add_argument("--runner-sha256", required=True)
    p.add_argument("--devices", default=",".join(map(str, range(16))))
    p.add_argument("--max-concurrency", type=int, default=4)
    p.add_argument("--max-pending", type=int, default=128)
    p.add_argument("--port", type=int, default=8882)
    p.add_argument("--tested-mode", action="append", default=[])
    args = p.parse_args()
    os.umask(0o077)
    verify_media_tools()
    devices = tuple(int(d) for d in args.devices.split(","))
    runner = PoolCliRunner(args.runtime_root, args.source_manifest, args.runner_sha256)
    service = PoolService(
        args.state_root,
        read_token(args.token_file),
        runner,
        allocator=DevicePool(devices),
        max_concurrency=args.max_concurrency,
        max_pending=args.max_pending,
        tested_modes=args.tested_mode,
    )
    server = make_server(service, port=args.port)

    def drain(*_):
        if service.stopping.is_set():
            return
        service.begin_drain()

        def finish():
            for worker in service.workers:
                worker.join()
            server.shutdown()

        threading.Thread(target=finish, daemon=True).start()

    signal.signal(signal.SIGTERM, drain)
    signal.signal(signal.SIGINT, drain)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        service.close()


if __name__ == "__main__":
    main()
