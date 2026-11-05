"""Durable FIFO and exclusive-device behavior without loading model weights."""

from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import importlib
import json
import threading
import time
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

TOKEN = bytes(range(32)).hex()


def api_types():
    try:
        return importlib.import_module("services.pika_h3_pool.gateway")
    except ModuleNotFoundError:
        pytest.fail("The requested durable Pika device-pool API is not implemented")


def payload(key):
    return {
        "model": "pika-h3-flex-6step",
        "prompt": "A cat looks at the camera.",
        "seconds": 5,
        "size": "16:9",
        "resolution": "480p",
        "request_id": key,
    }


def until(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def pool(tmp_path, devices=(0, 1), probe=None):
    return api_types().DevicePool(
        devices,
        lock_root=tmp_path / "locks",
        memory_probe=probe or (lambda: {d: 96 * 2**30 for d in devices}),
    )


def test_single_worker_claims_fifo_and_reports_queue_position(tmp_path):
    started, release = [], threading.Event()

    def runner(job, request, directory, record_pid, lease):
        started.append(job)
        release.wait(3)
        raise RuntimeError("Controlled external inference failure")

    service = api_types().PoolService(
        tmp_path / "state", TOKEN, runner, allocator=pool(tmp_path), max_concurrency=1
    )
    try:
        first = service.submit(payload("first"))
        until(lambda: len(started) == 1)
        second, third = (
            service.submit(payload("second")),
            service.submit(payload("third")),
        )
        assert service.status(second)["queue_position"] == 1
        assert service.status(third)["queue_position"] == 2
        assert service.queue_status()["queued"] == 2
        release.set()
        until(lambda: service.status(third)["status"] == "failed")
        assert started == [first, second, third]
    finally:
        release.set()
        service.close()


def test_concurrency_limit_keeps_devices_exclusive_and_extra_work_queued(tmp_path):
    lock, release = threading.Lock(), threading.Event()
    active, seen, started = set(), [], []

    def runner(job, request, directory, record_pid, lease):
        with lock:
            assert lease.device not in active
            active.add(lease.device)
            seen.append(set(active))
            started.append(job)
        release.wait(3)
        with lock:
            active.remove(lease.device)
        raise RuntimeError("Controlled inference end")

    service = api_types().PoolService(
        tmp_path / "state",
        TOKEN,
        runner,
        allocator=pool(tmp_path, tuple(range(16))),
        max_concurrency=2,
    )
    try:
        jobs = [service.submit(payload(str(i))) for i in range(5)]
        until(lambda: len(started) == 2)
        q = service.queue_status()
        assert q["active"] == 2 and q["queued"] == 3 and q["pool_size"] == 16
        assert len(q["active_devices"]) == 2
        release.set()
        until(lambda: all(service.status(j)["status"] == "failed" for j in jobs))
        assert max(map(len, seen)) == 2
    finally:
        release.set()
        service.close()


def test_same_id_is_durable_even_when_queue_full_and_changed_input_conflicts(tmp_path):
    def runner(*args):
        raise AssertionError("No worker enabled")

    service = api_types().PoolService(
        tmp_path / "state",
        TOKEN,
        runner,
        allocator=pool(tmp_path),
        max_pending=1,
        start_workers=False,
    )
    try:
        with ThreadPoolExecutor(max_workers=8) as workers:
            ids = list(
                workers.map(lambda _: service.submit(payload("same")), range(16))
            )
        assert len(set(ids)) == 1
        assert service.submit(payload("same")) == ids[0]
        with pytest.raises(OverflowError):
            service.submit(payload("new"))
        with pytest.raises(FileExistsError):
            service.submit({**payload("same"), "prompt": "different"})
    finally:
        service.close()


def test_restart_resumes_only_queued_work_not_uncertain_running_work(tmp_path):
    calls = []

    def runner(job, *args):
        calls.append(job)
        raise RuntimeError("Controlled failure")

    cls = api_types().PoolService
    first = cls(
        tmp_path / "state", TOKEN, runner, allocator=pool(tmp_path), start_workers=False
    )
    queued = first.submit(payload("queued"))
    uncertain = first.submit(payload("uncertain"))
    # Persist the launch boundary exactly as a crashed dispatcher would have done.
    first.db.execute("UPDATE jobs SET status='in_progress' WHERE id=?", (uncertain,))
    first.db.commit()
    first.close()
    again = cls(
        tmp_path / "state", TOKEN, runner, allocator=pool(tmp_path), max_concurrency=1
    )
    try:
        until(lambda: again.status(queued)["status"] == "failed")
        assert again.status(uncertain)["status"] == "unknown"
        assert again.submit(payload("uncertain")) == uncertain
        assert calls == [queued]
    finally:
        again.close()


def test_external_device_lease_is_skipped_until_released(tmp_path):
    p = pool(tmp_path)
    lockfile = tmp_path / "locks/easel-h3-ppu-0.lock"
    fd = lockfile.open("a")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    second = p.acquire()
    assert second.device == 1
    assert p.acquire() is None
    second.close()
    fd.close()
    lease = p.acquire()
    assert lease is not None
    lease.close()


def test_failed_hardware_observation_does_not_launch_or_lose_the_queue(tmp_path):
    calls = []

    def unavailable():
        raise RuntimeError("SMI observation unavailable")

    service = api_types().PoolService(
        tmp_path / "state",
        TOKEN,
        lambda *a: calls.append(a),
        allocator=pool(tmp_path, probe=unavailable),
        max_concurrency=1,
    )
    try:
        job = service.submit(payload("wait"))
        time.sleep(0.15)
        assert service.status(job)["status"] == "queued"
        assert service.queue_status()["active"] == 0 and calls == []
    finally:
        service.close()


def test_host_memory_reservation_limits_concurrency_before_loading_weights(tmp_path):
    allocator = api_types().DevicePool(
        [0, 1],
        lock_root=tmp_path / "locks",
        memory_probe=lambda: {0: 96 * 2**30, 1: 96 * 2**30},
        host_memory_probe=lambda: 200 * 2**30,
    )
    first = allocator.acquire()
    assert first is not None
    try:
        assert allocator.acquire() is None
        assert allocator.last_error == "insufficient_host_memory"
    finally:
        first.close()
    next_lease = allocator.acquire()
    assert next_lease is not None
    next_lease.close()


def test_queued_cancel_releases_capacity_and_does_not_execute(tmp_path):
    service = api_types().PoolService(
        tmp_path / "state",
        TOKEN,
        lambda *a: None,
        allocator=pool(tmp_path),
        max_pending=1,
        start_workers=False,
    )
    try:
        old = service.submit(payload("cancel"))
        assert service.cancel(old)["status"] == "cancelled"
        new = service.submit(payload("next"))
        assert service.status(new)["status"] == "queued"
        assert service.submit(payload("cancel")) == old
    finally:
        service.close()


def test_http_authentication_queue_status_and_invalid_device_input(tmp_path):
    module = api_types()
    service = module.PoolService(
        tmp_path / "state",
        TOKEN,
        lambda *a: None,
        allocator=pool(tmp_path),
        start_workers=False,
    )
    server = module.make_server(service, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path, data=None, token=TOKEN):
        headers = {"Authorization": "Bearer " + token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        return json.load(
            urlopen(
                Request(
                    base + path,
                    data=None if data is None else json.dumps(data).encode(),
                    headers=headers,
                )
            )
        )

    try:
        with pytest.raises(HTTPError) as denied:
            request("/v1/queue", token="wrong")
        assert denied.value.code == 401
        result = request("/v1/videos", payload("http"))
        assert result["status"] == "queued" and result["queue_position"] == 1
        assert request("/v1/queue")["queued"] == 1
        assert request("/health")["pool_size"] == 2
        with pytest.raises(HTTPError) as invalid:
            request("/v1/videos", {**payload("bad"), "device": 1})
        assert invalid.value.code == 400
        assert "prompt" not in json.dumps(request("/v1/videos"))
        assert (
            request("/v1/videos/" + result["id"] + "/cancel", {})["status"]
            == "cancelled"
        )
    finally:
        server.shutdown()
        server.server_close()
        service.close()


def test_original_output_can_be_revalidated_without_another_model_launch(tmp_path):
    from services.pika_h3_ppu.validation import SOURCE_COMMIT, request_fingerprint

    calls, ready = [], threading.Event()
    content = b"original completed bytes, codec validator tested independently"

    def runner(job, request, directory, record_pid, lease):
        calls.append(job)
        (directory / "output.mp4").write_bytes(content)
        (directory / "output.json").write_text("{}")
        audit = {
            "schema": 1,
            "job_id": job,
            "request_sha256": request_fingerprint(request),
            "source_commit": SOURCE_COMMIT,
            "manifest_sha256": "a" * 64,
            "runner_sha256": "b" * 64,
            "device": lease.device,
            "load_mode": "fresh_process_nonresident",
            "admission_free_bytes": 96 * 2**30,
        }
        (directory / "execution.json").write_text(json.dumps(audit))
        return audit

    def validator(path, request):
        if not ready.is_set():
            raise FileNotFoundError("Media dependency missing")
        return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    service = api_types().PoolService(
        tmp_path / "state",
        TOKEN,
        runner,
        allocator=pool(tmp_path),
        max_concurrency=1,
        validator=validator,
    )
    try:
        job = service.submit(payload("original"))
        until(lambda: service.status(job)["status"] == "failed")
        ready.set()
        service.reconcile(job)
        until(lambda: service.status(job)["status"] == "completed")
        assert calls == [job]
        assert service.status(job)["receipt"]["recovered_original_output"] is True
        with service.open_content(job) as stream:
            assert stream.read() == content
    finally:
        service.close()


def test_cli_rejects_missing_media_tools_before_opening_a_runtime(tmp_path):
    import os

    args = [
        sys.executable,
        "-m",
        "services.pika_h3_pool.gateway",
        "--runtime-root",
        str(tmp_path / "not-a-runtime"),
        "--state-root",
        str(tmp_path / "state"),
        "--token-file",
        str(tmp_path / "token"),
        "--source-manifest",
        str(tmp_path / "manifest"),
        "--runner-sha256",
        "a" * 64,
    ]
    result = subprocess.run(
        args,
        env={**os.environ, "PATH": str(tmp_path / "empty")},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Media tools unavailable" in result.stderr
    assert not (tmp_path / "state").exists()
