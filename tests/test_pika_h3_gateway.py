"""Gateway safety contract; no model, GPU or network credentials required."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import hashlib
import shutil
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
import pytest

from services.pika_h3_ppu.gateway import VideoService, make_server, read_token
from services.pika_h3_ppu.validation import (
    ADAPTER_REVISION, ADAPTER_SHA, BASE_REVISION, BUCKET_SHA, KNOTS, MODEL,
    SOURCE_COMMIT, TIMINGS, normalize, request_fingerprint, validate_result,
)


def payload(**extra):
    return {"model": MODEL, "prompt": "A cat meows naturally.", "seconds": 5,
            "size": "16:9", "resolution": "480p", "seed": 42, **extra}


def image_url():
    stream = io.BytesIO()
    Image.new("RGB", (32, 32)).save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


@pytest.mark.parametrize("extra", [
    {"model": "minimax-h3-dense8"}, {"seconds": True}, {"seconds": 4},
    {"duration": 5}, {"steps": 6}, {"device": 0}, {"size": "832x480"},
    {"images": [{"data_url": "https://example.com/a.png"}]},
    {"image": image_url()}, {"audios": [{"data_url": "data:audio/wav;base64,QQ=="}]},
    {"images": [{"data_url": image_url()}, {"data_url": image_url()}]},
    {"images": [{"data_url": image_url().replace("image/png", "image/jpeg")}]},
])
def test_rejects_unsupported_requests(extra):
    with pytest.raises(ValueError):
        normalize(payload(**extra))


def test_image_canonical_hash_and_duration_alias():
    a, raw = normalize(payload(images=[{"data_url": image_url()}]))
    b, _ = normalize({**{k: v for k, v in payload().items() if k != "seconds"},
                      "duration": 5, "images": [{"data_url": image_url()}]})
    assert a == b and raw.startswith(b"\x89PNG")
    assert a["task"] == "i2va" and "base64" not in json.dumps(a)


def wait_status(service, job, expected):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        if service.status(job)["status"] == expected:
            return
        time.sleep(.01)
    assert service.status(job)["status"] == expected


def test_concurrent_idempotency_and_failure_never_replays(tmp_path):
    calls = []
    def fail(job, request, directory, record_pid):
        calls.append(job)
        raise RuntimeError("secret prompt and absolute paths must stay private")
    service = VideoService(tmp_path / "state", "x" * 32, fail)
    with ThreadPoolExecutor(max_workers=12) as pool:
        jobs = list(pool.map(lambda _: service.submit(payload(request_id="same")), range(30)))
    assert len(set(jobs)) == 1
    wait_status(service, jobs[0], "failed")
    assert len(calls) == 1
    diagnostic = json.loads((service.root / jobs[0] / "failure.json").read_text())
    assert diagnostic["phase"] == "execution" and diagnostic["error_type"] == "RuntimeError"
    assert "secret" not in json.dumps(diagnostic)
    with pytest.raises(FileExistsError):
        service.submit(payload(request_id="same", prompt="different"))
    service.close()
    again = VideoService(tmp_path / "state", "x" * 32, fail)
    assert again.submit(payload(request_id="same")) == jobs[0]
    assert again.status(jobs[0])["status"] == "failed"
    assert "secret" not in json.dumps(again.status(jobs[0]))
    assert len(calls) == 1
    again.close()


def test_restart_unknown_is_not_replayed(tmp_path):
    calls = []
    service = VideoService(tmp_path / "state", "x" * 32,
                           lambda *args: calls.append(args), start_worker=False)
    job = service.submit(payload(request_id="original"))
    service.close()
    again = VideoService(tmp_path / "state", "x" * 32, lambda *args: calls.append(args))
    assert again.submit(payload(request_id="original")) == job
    assert again.status(job)["status"] == "unknown"
    assert not calls
    again.close()


def test_shutdown_drains_active_and_keeps_idempotent_queries(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def active(*args):
        entered.set()
        release.wait(5)
        raise RuntimeError("fixture")
    service = VideoService(tmp_path / "state", "x" * 32, active, max_pending=1)
    job = service.submit(payload(request_id="active"))
    assert entered.wait(2)
    closer = threading.Thread(target=service.close)
    closer.start()
    while not service.stopping.is_set():
        time.sleep(.01)
    assert service.submit(payload(request_id="active")) == job
    with pytest.raises(RuntimeError, match="draining"):
        service.submit(payload(request_id="new"))
    assert closer.is_alive()
    release.set()
    closer.join(3)
    assert not closer.is_alive()


def test_auth_loopback_private_token_and_safe_status(tmp_path):
    tokenfile = tmp_path / "token"
    tokenfile.write_text("x" * 32)
    tokenfile.chmod(0o600)
    assert read_token(tokenfile) == "x" * 32
    tokenfile.chmod(0o644)
    with pytest.raises(ValueError):
        read_token(tokenfile)
    service = VideoService(tmp_path / "state", "x" * 32, lambda *args: None,
                           start_worker=False)
    with pytest.raises(ValueError):
        make_server(service, "0.0.0.0", 0)
    server = make_server(service, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    for path in ("/health", "/v1/models", "/v1/videos"):
        with pytest.raises(HTTPError) as err:
            urlopen(base + path)
        assert err.value.code == 401
    req = Request(base + "/v1/videos", data=json.dumps(payload(request_id="http")).encode(),
                  headers={"Authorization": "Bearer " + "x" * 32, "Content-Type": "application/json"})
    result = json.load(urlopen(req))
    assert result["status"] == "queued" and result["model"] == MODEL
    assert "prompt" not in result and "path" not in json.dumps(result)
    req = Request(base + "/v1/videos", headers={"Authorization": "Bearer " + "x" * 32})
    assert json.load(urlopen(req))["data"][0]["id"] == result["id"]
    server.shutdown()
    server.server_close()
    service.close()


@pytest.fixture
def result_fixture(tmp_path):
    output = tmp_path / "output.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=832x480:r=24",
                    "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=32000", "-frames:v", "124",
                    "-t", str(124/24), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-ac", "2", str(output)], check=True)
    metadata = {"task": "t2va", "prompt": payload()["prompt"], "seed": 42,
                "requested_seconds": 5, "resolution": "480p", "aspect": "16:9",
                "geometry": {"height": 480, "width": 832, "num_frames": 124,
                             "num_latent_frames": 37, "latent_height": 30,
                             "latent_width": 52, "num_audio_latents": 207},
                "adapter_revision": ADAPTER_REVISION, "adapter_sha256": ADAPTER_SHA,
                "base": {"repo_id": "MiniMaxAI/MiniMax-H3", "revision": BASE_REVISION},
                "bucket_sha256": BUCKET_SHA, "noise_version": "pika-flex-noise-v1",
                "numerics_policy": "bf16_conditioning_v1", "reference_order": [],
                "input_sha256": {"first_image": None, "last_image": None, "references": []},
                "sampling": {"knots": KNOTS, "video_shift": 12., "audio_shift": 3., "method": "renoise"},
                "torch": "2.9.0", "gpu": "PPU-ZW810E", "sampling_peak_allocated_bytes": 70 * 2**30,
                "dependencies": {"diffusers": "0.40.0.dev0", "transformers": "5.15.0",
                                 "peft": "0.20.0", "av": "18.1.0", "safetensors": "0.8.0"},
                "timings": {name: .1 for name in TIMINGS}}
    output.with_suffix(".json").write_text(json.dumps(metadata))
    return output


def test_real_media_and_pinned_metadata_required(result_fixture):
    request, _ = normalize(payload())
    receipt = validate_result(result_fixture, request)
    assert receipt["frames"] == 124 and receipt["native_audio"] is True
    assert receipt["nfe"] == 6 and receipt["duration"] == pytest.approx(124/24)
    assert receipt["runtime"]["torch"] == "2.9.0"
    metadata = json.loads(result_fixture.with_suffix(".json").read_text())
    metadata["adapter_revision"] = "wrong"
    result_fixture.with_suffix(".json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError):
        validate_result(result_fixture, request)


def test_symlink_state_and_result_rejected(tmp_path, result_fixture):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        VideoService(alias / "state", "x" * 32, lambda *args: None)
    linked = tmp_path / "linked.mp4"
    linked.symlink_to(result_fixture)
    with pytest.raises(ValueError):
        validate_result(linked, normalize(payload())[0])


def test_completed_content_is_checked_and_cannot_escape(tmp_path, result_fixture):
    def run(job, request, directory, record_pid):
        shutil.copyfile(result_fixture, directory / "output.mp4")
        shutil.copyfile(result_fixture.with_suffix(".json"), directory / "output.json")
        return {"schema": 1, "job_id": job, "request_sha256": request_fingerprint(request),
                "source_commit": SOURCE_COMMIT, "manifest_sha256": "a" * 64, "runner_sha256": "b" * 64,
                "device": 0, "load_mode": "fresh_process_nonresident", "admission_free_bytes": 84 * 2**30}
    service = VideoService(tmp_path / "state", "x" * 32, run)
    job = service.submit(payload())
    wait_status(service, job, "completed")
    with service.open_content(job) as stream:
        assert stream.read(8) == result_fixture.read_bytes()[:8]
    output = service.root / job / "output.mp4"
    output.unlink()
    output.symlink_to(result_fixture)
    with pytest.raises(ValueError):
        service.open_content(job)
    with pytest.raises(KeyError):
        service.open_content("../output")
    service.close()


def test_bounded_queue_and_no_launch_after_draining(tmp_path):
    service = VideoService(tmp_path / "state", "x" * 32, lambda *args: None,
                           max_pending=1, start_worker=False)
    service.submit(payload(request_id="one"))
    with pytest.raises(OverflowError):
        service.submit(payload(request_id="two"))
    service.close()


def test_source_manifest_detects_drift_and_extra_python(tmp_path):
    from services.pika_h3_ppu.executor import validate_source
    root = tmp_path / "runtime"
    code = root / "code" / "src/pika_h3"
    code.mkdir(parents=True)
    files = {}
    for name in ("pipeline.py", "cli.py"):
        (code / name).write_text("# pinned fixture\n")
        files[f"src/pika_h3/{name}"] = hashlib.sha256((code / name).read_bytes()).hexdigest()
    manifest = root / "source.json"
    manifest.write_text(json.dumps({"source_commit": SOURCE_COMMIT, "files": files}))
    assert validate_source(root, manifest)["source_commit"] == SOURCE_COMMIT
    (code / "extra.py").write_text("# unverified\n")
    with pytest.raises(ValueError):
        validate_source(root, manifest)
    (code / "extra.py").unlink()
    (code / "pipeline.py").write_text("# changed\n")
    with pytest.raises(ValueError):
        validate_source(root, manifest)


def test_existing_unprivate_state_is_not_chmodded(tmp_path):
    root = tmp_path / "existing"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(ValueError):
        VideoService(root, "x" * 32, lambda *args: None)
    assert root.stat().st_mode & 0o777 == 0o755


def test_existing_deployment_manifest_format_supported(tmp_path):
    from services.pika_h3_ppu.executor import validate_source
    root = tmp_path / "runtime"
    code = root / "code" / "src/pika_h3"
    code.mkdir(parents=True)
    files = []
    for name in ("pipeline.py", "cli.py"):
        (code / name).write_bytes(b"# pinned fixture\n")
        files.append({"path": f"src/pika_h3/{name}", "size": (code / name).stat().st_size,
                      "sha256": hashlib.sha256((code / name).read_bytes()).hexdigest()})
    manifest = root / "source.json"
    manifest.write_text(json.dumps({"version": 1, "repo": "https://github.com/Pika-Labs/Pika-H3-Flex",
                                    "commit": SOURCE_COMMIT, "files": files}))
    assert validate_source(root, manifest)["source_commit"] == SOURCE_COMMIT


def test_device_locks_are_exclusive_and_inherited(tmp_path):
    from services.pika_h3_ppu.executor import device_leases
    import sys
    with device_leases(3, lock_root=tmp_path) as fds:
        assert len(fds) == 2
        with pytest.raises(BlockingIOError):
            with device_leases(3, lock_root=tmp_path):
                pass
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.4)"], pass_fds=fds)
    with pytest.raises(BlockingIOError):
        with device_leases(3, lock_root=tmp_path):
            pass
    process.wait(timeout=3)
    with device_leases(3, lock_root=tmp_path):
        pass


def test_executor_low_memory_never_launches_and_prompt_is_not_argv(tmp_path, monkeypatch):
    from services.pika_h3_ppu.executor import CliExecutor, MIN_FREE_BYTES
    executor = object.__new__(CliExecutor)
    executor.root, executor.device = tmp_path, 7
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, json.dumps({"free_bytes": MIN_FREE_BYTES - 1}).encode()))
    with pytest.raises(RuntimeError, match="84 GiB"):
        executor._memory_probe({}, ())


def test_real_result_rejects_video_without_native_audio(result_fixture):
    metadata = result_fixture.with_suffix(".json").read_bytes()
    no_audio = result_fixture.parent / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(result_fixture), "-an", "-c:v", "copy", str(no_audio)], check=True)
    no_audio.with_suffix(".json").write_bytes(metadata)
    with pytest.raises(ValueError, match="native audio"):
        validate_result(no_audio, normalize(payload())[0])


def test_restart_valid_output_requires_original_execution_attestation(tmp_path, result_fixture):
    service = VideoService(tmp_path / "state", "x" * 32, lambda *args: None, start_worker=False)
    job = service.submit(payload(request_id="original"))
    directory = service.root / job
    shutil.copyfile(result_fixture, directory / "output.mp4")
    shutil.copyfile(result_fixture.with_suffix(".json"), directory / "output.json")
    service.close()
    calls = []
    again = VideoService(tmp_path / "state", "x" * 32, lambda *args: calls.append(args))
    assert again.status(job)["status"] == "unknown" and not calls
    request, _ = normalize(payload(request_id="original"))
    audit = {"schema": 1, "job_id": job, "request_sha256": request_fingerprint(request),
             "source_commit": SOURCE_COMMIT, "manifest_sha256": "a" * 64, "runner_sha256": "b" * 64,
             "device": 0, "load_mode": "fresh_process_nonresident", "admission_free_bytes": 84 * 2**30}
    (directory / "execution.json").write_text(json.dumps(audit))
    wait_status(again, job, "completed")
    assert not calls
    assert again.status(job)["receipt"]["recovered_original_output"] is True
    again.close()


def test_runner_hash_rejects_changed_entrypoint(tmp_path):
    from services.pika_h3_ppu.executor import validate_runner
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/bash\nexit 0\n")
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    assert validate_runner(tmp_path, digest) == digest
    script.write_text("#!/bin/bash\nexit 1\n")
    with pytest.raises(ValueError, match="runner"):
        validate_runner(tmp_path, digest)


def test_cli_argv_uses_private_files_and_inherits_explicit_device_locks(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from services.pika_h3_ppu import executor as module
    runner = object.__new__(module.CliExecutor)
    runner.root, runner.device = tmp_path / "runtime", 7
    runner.source_manifest, runner.runner_sha256 = tmp_path / "source.json", "b" * 64
    monkeypatch.setattr(module, "validate_source", lambda *args:
                        {"source_commit": SOURCE_COMMIT, "manifest_sha256": "a" * 64})
    monkeypatch.setattr(module, "validate_runner", lambda *args: "b" * 64)
    @contextmanager
    def lease(device):
        assert device == 7
        yield (91, 92)
    monkeypatch.setattr(module, "device_leases", lease)
    def probe(env, fds):
        assert env["CUDA_VISIBLE_DEVICES"] == "7" and env["H3_DEVICE"] == "7"
        assert fds == (91, 92)
        return {"free_bytes": 85 * 2**30}
    runner._memory_probe = probe
    launches = []
    def launch(command, **kwargs):
        launches.append((command, kwargs))
        assert kwargs["pass_fds"] == (91, 92) and kwargs["start_new_session"] is True
        return SimpleNamespace(pid=999999999, wait=lambda: 0)
    monkeypatch.setattr(subprocess, "Popen", launch)
    request, raw = normalize(payload(prompt="Literal $(bad) `do not execute`", images=[{"data_url": image_url()}]))
    directory = tmp_path / "job"
    directory.mkdir(mode=0o700)
    (directory / "prompt.txt").write_text(request["prompt"])
    (directory / "first.png").write_bytes(raw)
    recorded = []
    audit = runner("video_" + "1" * 32, request, directory, lambda *args: recorded.append(args))
    command, kwargs = launches[0]
    assert request["prompt"] not in command and "--prompt-file" in command and "--first-image" in command
    assert command[:3] == ["bash", str(runner.root / "run.sh"), "i2va"]
    assert "shell" not in kwargs and audit["runner_sha256"] == "b" * 64
    assert audit["job_id"] == "video_" + "1" * 32 and len(recorded) == 1
    assert (directory / "execution.json").stat().st_mode & 0o777 == 0o600


def test_recovery_verification_does_not_block_status_or_submission(tmp_path):
    service = VideoService(tmp_path / "state", "x" * 32, lambda *args: None, start_worker=False)
    job = service.submit(payload(request_id="recovering"))
    directory = service.root / job
    for name in ("output.mp4", "output.json"):
        (directory / name).write_bytes(b"fixture")
    request, _ = normalize(payload(request_id="recovering"))
    audit = {"schema": 1, "job_id": job, "request_sha256": request_fingerprint(request),
             "source_commit": SOURCE_COMMIT, "manifest_sha256": "a" * 64, "runner_sha256": "b" * 64,
             "device": 0, "load_mode": "fresh_process_nonresident", "admission_free_bytes": 84 * 2**30}
    (directory / "execution.json").write_text(json.dumps(audit))
    service.close()
    entered, release = threading.Event(), threading.Event()
    def slow_validation(*args):
        entered.set()
        release.wait(3)
        return {"sha256": "c" * 64}
    again = VideoService(tmp_path / "state", "x" * 32, lambda *args: None,
                         start_worker=False, validator=slow_validation)
    assert entered.wait(1)
    start = time.monotonic()
    assert again.status(job)["status"] == "unknown"
    assert again.submit(payload(request_id="new")) != job
    assert time.monotonic() - start < .25
    release.set()
    wait_status(again, job, "completed")
    again.close()
