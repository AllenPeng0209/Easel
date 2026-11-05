"""Exercise the CLI boundary with a real child and a pinned tiny runtime."""

import hashlib
import importlib
import json
import sys

import pytest

from services.pika_h3_ppu.validation import SOURCE_COMMIT, normalize


def runner_type():
    try:
        return importlib.import_module("services.pika_h3_pool.runner").PoolCliRunner
    except ModuleNotFoundError:
        pytest.fail("The pooled official-CLI runner is not implemented")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    source = root / "code/src/pika_h3"
    source.mkdir(parents=True)
    (source / "pipeline.py").write_text("# pinned fixture\n")
    (source / "cli.py").write_text("# pinned fixture\n")
    files = {
        str(p.relative_to(root / "code")): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.glob("*.py")
    }
    manifest = root / "source.json"
    manifest.write_text(json.dumps({"source_commit": SOURCE_COMMIT, "files": files}))
    (root / "venv/bin").mkdir(parents=True)
    (root / "venv/bin/python").symlink_to(sys.executable)
    # Real argv handling and child output. No model or GPU dependency is hidden.
    run = root / "run.sh"
    run.write_text("""#!/bin/bash
exec "$TEST_PIKA_PYTHON" -c 'import sys,json,pathlib; a=sys.argv[1:]; p=pathlib.Path(a[a.index("--output")+1]); p.write_bytes(b"fixture-not-accepted-as-media"); p.with_suffix(".args.json").write_text(json.dumps(a))' "$@"
""")
    monkeypatch.setenv("TEST_PIKA_PYTHON", sys.executable)
    from services.pika_h3_ppu.executor import CliExecutor

    monkeypatch.setattr(
        CliExecutor,
        "_memory_probe",
        lambda self, env, fds: {
            "free_bytes": 96 * 2**30,
            "torch": "2.9.0",
            "device_count": 1,
        },
    )
    return root, manifest, hashlib.sha256(run.read_bytes()).hexdigest()


def test_real_child_receives_fixed_cli_parameters_and_offload(runtime, tmp_path):
    root, manifest, digest = runtime
    from services.pika_h3_pool.gateway import DevicePool

    pool = DevicePool(
        [3], lock_root=tmp_path / "locks", memory_probe=lambda: {3: 96 * 2**30}
    )
    lease = pool.acquire()
    directory = tmp_path / "job"
    directory.mkdir()
    request, _ = normalize(
        {
            "model": "pika-h3-flex-6step",
            "prompt": "A cat. $(touch forbidden)",
            "seconds": 10,
            "size": "9:16",
            "resolution": "480p",
            "seed": 123,
        }
    )
    (directory / "prompt.txt").write_text(request["prompt"])
    pids = []
    try:
        audit = runner_type()(root, manifest, digest)(
            "video_" + "a" * 32,
            request,
            directory,
            lambda pid, identity: pids.append(pid),
            lease,
        )
        args = json.loads((directory / "output.args.json").read_text())
        assert args[0] == "t2va" and args[args.index("--duration") + 1] == "10"
        assert args[args.index("--seed") + 1] == "123" and "--cpu-offload" in args
        assert args[args.index("--prompt-file") + 1] == str(directory / "prompt.txt")
        assert audit["device"] == 3 and audit["request_sha256"]
        assert len(pids) == 1 and not (directory / "forbidden").exists()
        assert (
            json.loads((directory / "execution.json").read_text())["job_id"]
            == "video_" + "a" * 32
        )
    finally:
        lease.close()


def test_changed_source_and_existing_output_never_launch(runtime, tmp_path):
    root, manifest, digest = runtime
    from services.pika_h3_pool.gateway import DevicePool

    lease = DevicePool(
        [0], lock_root=tmp_path / "locks", memory_probe=lambda: {0: 96 * 2**30}
    ).acquire()
    runner = runner_type()(root, manifest, digest)
    directory = tmp_path / "job"
    directory.mkdir()
    request, _ = normalize({"model": "pika-h3-flex-6step", "prompt": "A cat."})
    (directory / "prompt.txt").write_text(request["prompt"])
    (root / "code/src/pika_h3/pipeline.py").write_text("changed\n")
    try:
        with pytest.raises(ValueError):
            runner("video_" + "b" * 32, request, directory, lambda *a: None, lease)
        assert not (directory / "execution.json").exists()
        (directory / "output.mp4").write_bytes(b"original")
        with pytest.raises(RuntimeError):
            runner("video_" + "b" * 32, request, directory, lambda *a: None, lease)
        assert (directory / "output.mp4").read_bytes() == b"original"
    finally:
        lease.close()
