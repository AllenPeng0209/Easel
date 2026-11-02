from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "video_workshop.py"


@pytest.fixture
def cli():
    if not SCRIPT.is_file():
        pytest.skip("CLI is not implemented yet; the entrypoint test captures that missing behavior")
    spec = importlib.util.spec_from_file_location("video_workshop_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_can_be_invoked_from_a_terminal():
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


class Reply(io.BytesIO):
    status = 200


def test_create_project_sends_the_actual_brief_to_easel(cli, monkeypatch, capsys):
    def open_request(request, timeout):
        assert request.full_url == "http://127.0.0.1:7860/api/workshop/projects"
        assert request.method == "POST"
        assert json.loads(request.data) == {
            "title": "咖啡产品片", "brief": "前三秒展示产品，保留人工修改",
            "reference_url": "https://example.com/reference", "aspect_ratio": "9:16",
        }
        return Reply(b'{"id":"project-1","revision":1}')

    monkeypatch.setattr(cli, "open_request", open_request)
    assert cli.main(["create", "--title", "咖啡产品片", "--brief", "前三秒展示产品，保留人工修改",
                     "--reference-url", "https://example.com/reference"]) == 0
    assert json.loads(capsys.readouterr().out) == {"id": "project-1", "revision": 1}


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1.evil.test:7860",
                                "http://user:password@127.0.0.1:7860", "http://127.0.0.1:7860/?key=secret"])
def test_external_or_credential_bearing_easel_url_is_rejected_before_request(cli, monkeypatch, url):
    monkeypatch.setattr(cli, "open_request", lambda *_a, **_k: pytest.fail("must not connect"))
    assert cli.main(["--base-url", url, "status"]) == 2


def test_register_delivery_preserves_revision_and_only_accepts_output_relative_path(cli, monkeypatch, capsys):
    def open_request(request, timeout):
        assert request.full_url.endswith("/api/workshop/projects/project-1/deliverables")
        assert json.loads(request.data) == {"expected_revision": 7, "path": "咖啡产品片/final.mp4"}
        return Reply(b'{"revision":8}')

    monkeypatch.setattr(cli, "open_request", open_request)
    assert cli.main(["register-delivery", "project-1", "--expected-revision", "7", "--path", "咖啡产品片/final.mp4"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 8
    assert cli.main(["register-delivery", "project-1", "--expected-revision", "7", "--path", "../private.mp4"]) == 2


def test_easel_conflict_is_not_retried_or_hidden(cli, monkeypatch, capsys):
    calls = []

    def open_request(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 409, "Conflict", {}, io.BytesIO(b'{"detail":"secret-upstream-body"}'))

    monkeypatch.setattr(cli, "open_request", open_request)
    assert cli.main(["status"]) == 4
    assert len(calls) == 1
    assert "secret-upstream-body" not in capsys.readouterr().err


def test_delivery_preserves_the_real_engine_revision_from_its_render_receipt(cli, monkeypatch):
    def open_request(request, timeout):
        assert json.loads(request.data) == {
            "expected_revision": 7, "engine_revision": 12, "path": "咖啡产品片/final.mp4",
        }
        return Reply(b'{"revision":8}')

    monkeypatch.setattr(cli, "open_request", open_request)
    try:
        result = cli.main(["register-delivery", "project-1", "--expected-revision", "7",
                           "--engine-revision", "12", "--path", "咖啡产品片/final.mp4"])
    except SystemExit as error:
        result = error.code
    assert result == 0


@pytest.fixture
def runtime(tmp_path):
    recorder = tmp_path / "runtime.py"
    recorder.write_text(
        "import json,sys\n"
        "args=sys.argv[1:]\n"
        "if args == ['cli','business','discover']:\n"
        " print(json.dumps([{'id':'read-project','summary':'Read project','path':'/projects','readOnly':True},"
        "{'id':'submit-video','summary':'Submit generation','path':'/tasks','readOnly':False,'submitsGeneration':True}]))\n"
        "elif args == ['cli','ops','--json']:\n"
        " print(json.dumps([{'id':'canvas.get','readOnly':True},{'id':'canvas.timeline.update','readOnly':False}]))\n"
        "elif args and args[0]=='mcp':\n"
        " print(json.dumps({'args':args,'input':sys.stdin.read()}))\n"
        "else:\n"
        " print(json.dumps({'args':args}))\n",
        encoding="utf-8",
    )
    shell = tmp_path / "runtime.sh"
    shell.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{recorder}" "$@"\n', encoding="utf-8")
    return shell


def test_business_list_filters_real_discovery_output(cli, runtime, monkeypatch, capsys):
    monkeypatch.setenv("EASEL_BEEFTV_RUNTIME_SCRIPT", str(runtime))
    assert cli.main(["business-list", "--query", "generation"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in result] == ["submit-video"]


def test_paid_business_write_requires_operation_id_before_it_is_dispatched(cli, runtime, monkeypatch, capsys):
    monkeypatch.setenv("EASEL_BEEFTV_RUNTIME_SCRIPT", str(runtime))
    assert cli.main(["business-call", "submit-video", "--params", '{"body":{"prompt":"coffee"}}']) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "operation-id" in captured.err


def test_json_arguments_are_not_interpreted_as_shell_commands(cli, runtime, tmp_path, monkeypatch, capfd):
    monkeypatch.setenv("EASEL_BEEFTV_RUNTIME_SCRIPT", str(runtime))
    marker = tmp_path / "must-not-exist"
    params = json.dumps({"body": {"prompt": f"$(touch {marker}) `touch {marker}` 中文"}}, ensure_ascii=False)
    assert cli.main(["business-call", "submit-video", "--params", params, "--operation-id", "attempt-123"]) == 0
    args = json.loads(capfd.readouterr().out)["args"]
    assert args == ["cli", "business", "call", "--tool", "submit-video", "--params", params,
                    "--operation-id", "attempt-123"]
    assert not marker.exists()


def test_operation_call_checks_known_schema_and_requires_id_for_write(cli, runtime, monkeypatch, capfd):
    monkeypatch.setenv("EASEL_BEEFTV_RUNTIME_SCRIPT", str(runtime))
    assert cli.main(["operation-call", "canvas.timeline.update", "--params", "{}"]) == 2
    assert cli.main(["operation-call", "unknown", "--params", "{}"]) == 2
    assert cli.main(["operation-call", "canvas.get", "--params", '{"canvasId":"canvas-1"}']) == 0
    args = json.loads(capfd.readouterr().out)["args"]
    assert args == ["cli", "ops", "call", "canvas.get", "--params", '{"canvasId":"canvas-1"}', "--json"]


def test_mcp_is_stdio_passthrough_to_official_server(cli, runtime):
    env = {**os.environ, "EASEL_BEEFTV_RUNTIME_SCRIPT": str(runtime)}
    initialize = '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
    result = subprocess.run([sys.executable, str(SCRIPT), "mcp", "--read-only"], input=initialize,
                            text=True, capture_output=True, env=env, check=True)
    assert json.loads(result.stdout) == {"args": ["mcp", "--read-only"], "input": initialize}
