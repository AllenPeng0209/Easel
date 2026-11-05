"""Authenticated loopback-only queue. Run with ``python -m ...gateway --help``."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import re
import signal
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .validation import (MAX_BODY, MODEL, SOURCE_COMMIT, normalize, read_regular,
                         request_fingerprint, safe_path, validate_result)

JOB_ID = re.compile(r"video_[a-f0-9]{32}")


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def read_token(path):
    path = safe_path(Path(path), regular=True)
    info = path.stat()
    if info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise ValueError("Token file must be owned by the service user with mode 0600")
    token = read_regular(path, 4096).decode("ascii").strip()
    if len(token) < 32 or not re.fullmatch(r"[A-Za-z0-9_.~-]+", token):
        raise ValueError("Token must contain at least 32 printable token characters")
    return token


class VideoService:
    def __init__(self, root, token, runner, *, max_pending=8, start_worker=True,
                 tested_modes=(), validator=validate_result):
        if not isinstance(token, str) or len(token) < 32 or type(max_pending) is not int or max_pending < 1:
            raise ValueError("A private token and positive queue limit are required")
        self.root = safe_path(Path(root))
        if self.root.exists() and self.root.stat().st_mode & 0o077:
            raise ValueError("Existing state root must already have mode 0700")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_uid != os.geteuid():
            raise ValueError("State root must belong to this user")
        self.root.chmod(0o700)
        lease_path = safe_path(self.root / "service.lock")
        self.lease_fd = os.open(lease_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.lease_fd)
            raise
        db_path = safe_path(self.root / "jobs.sqlite")
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        db_path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, request_id TEXT UNIQUE, fingerprint TEXT NOT NULL,
            request TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
            updated REAL NOT NULL, pid INTEGER, process_identity TEXT, receipt TEXT, error TEXT)""")
        self.db.execute("UPDATE jobs SET status='unknown',error='interrupted_no_automatic_retry' "
                        "WHERE status IN ('queued','in_progress')")
        self.db.commit()
        self.lock = threading.RLock()
        self.pending = queue.Queue(maxsize=max_pending)
        self.stopping = threading.Event()
        self.token, self.runner, self.validator = token, runner, validator
        self.tested_modes = list(tested_modes)
        self.worker = None
        self.closed = False
        self.max_pending = max_pending
        self.recovery_lock = threading.Lock()
        self.recovery_thread = None
        self.recovery_signatures = {}
        # Reconcile only original validated output; never launch during recovery.
        for row in self.db.execute("SELECT * FROM jobs WHERE status='unknown'").fetchall():
            self._maybe_reconcile(row)
        if start_worker:
            self.worker = threading.Thread(target=self._work, name="pika-single-worker", daemon=True)
            self.worker.start()

    def _directory(self, job_id):
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise KeyError("Unknown video")
        directory = safe_path(self.root / job_id)
        if not directory.is_dir() or directory.parent != self.root:
            raise KeyError("Unknown video")
        return directory

    def _update(self, job_id, status, error=None, receipt=None):
        with self.lock:
            self.db.execute("UPDATE jobs SET status=?,updated=?,error=?,receipt=? WHERE id=?",
                            (status, time.time(), error, json.dumps(receipt) if receipt else None, job_id))
            self.db.commit()

    def _reconcile(self, row):
        try:
            directory = self._directory(row["id"])
            if (directory / "output.mp4").exists() and (directory / "output.json").exists():
                audit = json.loads(read_regular(directory / "execution.json", 16 * 1024))
                self._validate_audit(audit, row["id"], json.loads(row["request"]))
                receipt = self.validator(directory / "output.mp4", json.loads(row["request"]))
                receipt["execution"] = audit
                receipt["recovered_original_output"] = True
                self._update(row["id"], "completed", receipt=receipt)
        except Exception:
            # An incomplete/corrupt result remains unknown, never queued again.
            return

    def _maybe_reconcile(self, row):
        """One bounded CPU validator; GET never performs full media decoding."""
        if self.stopping.is_set():
            return
        try:
            directory = self._directory(row["id"])
            signature = tuple((name, (directory / name).stat().st_size,
                                (directory / name).stat().st_mtime_ns,
                                (directory / name).stat().st_ino)
                              for name in ("output.mp4", "output.json", "execution.json"))
        except (KeyError, OSError, ValueError):
            return
        if self.recovery_signatures.get(row["id"]) == signature or not self.recovery_lock.acquire(False):
            return
        self.recovery_signatures[row["id"]] = signature
        def check():
            try:
                self._reconcile(row)
            finally:
                self.recovery_lock.release()
        self.recovery_thread = threading.Thread(target=check, name="pika-result-recovery", daemon=True)
        self.recovery_thread.start()

    def _validate_audit(self, audit, job_id, request):
        if (not isinstance(audit, dict) or audit.get("schema") != 1 or
                audit.get("job_id") != job_id or
                audit.get("request_sha256") != request_fingerprint(request) or
                audit.get("source_commit") != SOURCE_COMMIT or
                not isinstance(audit.get("manifest_sha256"), str) or
                not re.fullmatch("[0-9a-f]{64}", audit["manifest_sha256"]) or
                not isinstance(audit.get("runner_sha256"), str) or
                not re.fullmatch("[0-9a-f]{64}", audit["runner_sha256"]) or
                type(audit.get("device")) is not int or audit["device"] < 0 or
                audit.get("load_mode") != "fresh_process_nonresident" or
                type(audit.get("admission_free_bytes")) is not int or
                audit["admission_free_bytes"] < 84 * 2**30):
            raise ValueError("Original execution attestation is invalid")
        if hasattr(self.runner, "source") and audit["manifest_sha256"] != self.runner.source["manifest_sha256"]:
            raise ValueError("Original source attestation does not match this deployment")
        if hasattr(self.runner, "runner_sha256") and audit["runner_sha256"] != self.runner.runner_sha256:
            raise ValueError("Original runner attestation does not match this deployment")

    def submit(self, payload):
        request, image = normalize(payload)
        fingerprint = request_fingerprint(request)
        with self.lock:
            prior = None
            if request["request_id"]:
                prior = self.db.execute("SELECT id,fingerprint FROM jobs WHERE request_id=?",
                                        (request["request_id"],)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise FileExistsError("request_id belongs to a different input")
                return prior["id"]
            if self.stopping.is_set():
                raise RuntimeError("Gateway is draining; new requests are refused")
            if self.pending.full():
                raise OverflowError("Gateway queue is full")
            job_id = "video_" + uuid.uuid4().hex
            directory = self.root / job_id
            directory.mkdir(mode=0o700)
            private_write(directory / "prompt.txt", request["prompt"].encode())
            if image is not None:
                private_write(directory / ("first.png" if request["image_mime"] == "image/png" else "first.jpg"), image)
            now = time.time()
            self.db.execute("INSERT INTO jobs(id,request_id,fingerprint,request,status,created,updated) "
                            "VALUES(?,?,?,?,?,?,?)", (job_id, request["request_id"], fingerprint,
                            json.dumps(request, ensure_ascii=False), "queued", now, now))
            self.db.commit()  # Durable launch intent precedes worker visibility.
            self.pending.put_nowait(job_id)
            return job_id

    def _work(self):
        while not self.stopping.is_set():
            phase = "execution"
            try:
                job_id = self.pending.get(timeout=.1)
            except queue.Empty:
                continue
            with self.lock:
                if self.stopping.is_set():
                    self._update(job_id, "unknown", "shutdown_before_launch")
                    self.pending.task_done()
                    break
                row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                self._update(job_id, "in_progress")
            request = json.loads(row["request"])
            def record_pid(pid, identity):
                with self.lock:
                    self.db.execute("UPDATE jobs SET pid=?,process_identity=?,updated=? WHERE id=?",
                                    (pid, identity, time.time(), job_id))
                    self.db.commit()
            try:
                directory = self._directory(job_id)
                audit = self.runner(job_id, request, directory, record_pid)
                phase = "audit_validation"
                self._validate_audit(audit, job_id, request)
                phase = "result_validation"
                receipt = self.validator(directory / "output.mp4", request)
                receipt["execution"] = audit
                for name in ("output.mp4", "output.json"):
                    safe_path(directory / name, regular=True).chmod(0o600)
                self._update(job_id, "completed", receipt=receipt)
            except Exception as error:
                # Logs remain private. No arbitrary exception text, prompt or paths on API.
                try:
                    private_write(self._directory(job_id) / "failure.json", json.dumps({
                        "schema": 1, "phase": phase, "error_type": type(error).__name__,
                        "code": "generation_or_validation_failed_no_retry"}).encode())
                except (OSError, ValueError, KeyError):
                    pass
                self._update(job_id, "failed", "generation_or_validation_failed_no_retry")
            finally:
                self.pending.task_done()

    def status(self, job_id):
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise KeyError("Unknown video")
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown video")
            if row["status"] == "unknown":
                self._maybe_reconcile(row)
            request = json.loads(row["request"])
            result = {"id": job_id, "object": "video", "model": MODEL, "status": row["status"],
                      "created_at": row["created"], "updated_at": row["updated"],
                      "width": request["width"], "height": request["height"],
                      "frames": request["frames"], "duration": request["frames"] / 24,
                      "requested_seconds": request["seconds"], "fps": 24}
            if row["status"] == "completed":
                result["video_url"] = f"/v1/videos/{job_id}/content"
                result["receipt"] = json.loads(row["receipt"])
            if row["error"]:
                result["error"] = {"code": row["error"]}
            return result

    def list_jobs(self):
        with self.lock:
            return [{key: row[key] for key in ("id", "status", "created", "updated")}
                    for row in self.db.execute("SELECT id,status,created,updated FROM jobs "
                                               "ORDER BY created DESC LIMIT 100")]

    def open_content(self, job_id):
        result = self.status(job_id)
        if result["status"] != "completed":
            raise RuntimeError("Video is not completed")
        path = safe_path(self._directory(job_id) / "output.mp4", regular=True)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        stream = os.fdopen(fd, "rb")
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        if digest.hexdigest() != result["receipt"]["sha256"]:
            stream.close()
            raise ValueError("Validated video changed")
        stream.seek(0)
        return stream

    def begin_drain(self):
        with self.lock:
            self.stopping.set()
            self.db.execute("UPDATE jobs SET status='unknown',error='shutdown_before_launch' "
                            "WHERE status='queued'")
            self.db.commit()

    def close(self):
        self.begin_drain()
        if self.worker:
            self.worker.join()  # Active inference finishes; never kill the model process.
        if self.recovery_thread:
            self.recovery_thread.join()
        with self.lock:
            if not self.closed:
                self.db.close()
                os.close(self.lease_fd)
                self.closed = True


class Server(ThreadingHTTPServer):
    daemon_threads = True


def make_server(service, host="127.0.0.1", port=8882):
    if host != "127.0.0.1":
        raise ValueError("Gateway must bind exactly 127.0.0.1")
    class Handler(BaseHTTPRequestHandler):
        server_version = "PikaLoopback/1"
        def setup(self):
            super().setup()
            self.connection.settimeout(20)
        def log_message(self, *args):
            pass
        def _json(self, code, data):
            raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
        def _auth(self):
            received = self.headers.get("Authorization", "")
            if not hmac.compare_digest(received.encode(), ("Bearer " + service.token).encode()):
                self._json(401, {"error": {"code": "unauthorized"}})
                return False
            return True
        def do_POST(self):
            if not self._auth():
                return
            if self.path != "/v1/videos":
                self._json(404, {"error": {"code": "not_found"}})
                return
            try:
                if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected JSON with Content-Length")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError("Request body exceeds limit")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete request")
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("Duplicate JSON keys")
                        result[key] = value
                    return result
                payload = json.loads(raw, object_pairs_hook=unique)
                job = service.submit(payload)
                self._json(200, service.status(job))
            except FileExistsError:
                self._json(409, {"error": {"code": "idempotency_conflict"}})
            except OverflowError:
                self._json(429, {"error": {"code": "queue_full"}})
            except RuntimeError:
                self._json(503, {"error": {"code": "draining"}})
            except (ValueError, UnicodeError):
                self._json(400, {"error": {"code": "invalid_request"}})
        def do_GET(self):
            if not self._auth():
                return
            path = urlsplit(self.path).path
            if path == "/health":
                self._json(200, {"status": "draining" if service.stopping.is_set() else "ok",
                                  "model": MODEL, "resident": False, "model_ready": "not_probed",
                                  "tested_modes": service.tested_modes})
                return
            if path == "/v1/models":
                self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model",
                                  "owned_by": "operator", "resident": False,
                                  "supported_tasks": ["t2va", "i2va"], "resolutions": ["480p", "768p"],
                                  "tested_modes": service.tested_modes}]})
                return
            if path == "/v1/videos":
                self._json(200, {"object": "list", "data": service.list_jobs()})
                return
            match = re.fullmatch(r"/v1/videos/(video_[a-f0-9]{32})(?:/(status|content))?", path)
            if not match:
                self._json(404, {"error": {"code": "not_found"}})
                return
            try:
                if match[2] != "content":
                    self._json(200, service.status(match[1]))
                    return
                with service.open_content(match[1]) as stream:
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(os.fstat(stream.fileno()).st_size))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        self.wfile.write(block)
            except KeyError:
                self._json(404, {"error": {"code": "not_found"}})
            except (RuntimeError, ValueError, OSError):
                self._json(409, {"error": {"code": "content_not_available"}})
    return Server((host, port), Handler)


def main():
    from .executor import CliExecutor
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--runner-sha256", required=True,
                        help="SHA256 of the trusted operator run.sh recipe")
    parser.add_argument("--device", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    parser.add_argument("--port", type=int, default=8882)
    parser.add_argument("--max-pending", type=int, default=8)
    parser.add_argument("--tested-mode", action="append", default=[],
                        help="Operator evidence label, e.g. t2va:480p:16:9:5s")
    args = parser.parse_args()
    os.umask(0o077)
    executor = CliExecutor(args.runtime_root, args.device, args.source_manifest, args.runner_sha256)
    service = VideoService(args.state_root, read_token(args.token_file), executor,
                           max_pending=args.max_pending, tested_modes=args.tested_mode)
    server = make_server(service, args.host, args.port)
    def drain(*_args):
        if not service.stopping.is_set():
            service.begin_drain()
            # Keep HTTP queries available while the original active call finishes.
            def finish():
                if service.worker:
                    service.worker.join()
                server.shutdown()
            threading.Thread(target=finish, daemon=True).start()
    signal.signal(signal.SIGTERM, drain)
    signal.signal(signal.SIGINT, drain)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if not service.closed:
            service.close()


if __name__ == "__main__":
    main()
