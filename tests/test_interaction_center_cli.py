from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import urllib.error

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "interaction_center.py"


@pytest.fixture
def cli():
    if not SCRIPT.is_file():
        pytest.skip("The terminal entrypoint test covers the missing CLI")
    spec = importlib.util.spec_from_file_location("interaction_center_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Reply(io.BytesIO):
    status = 200


def test_cli_can_be_invoked_from_a_terminal():
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1.evil.test:7860",
                                "http://user:private@127.0.0.1:7860", "http://127.0.0.1:7860/?token=private"])
def test_scope_reads_cannot_be_redirected_to_an_external_or_credential_bearing_origin(cli, monkeypatch, url):
    monkeypatch.setattr(cli, "open_request", lambda *_a, **_k: pytest.fail("must not connect"))
    assert cli.main(["--base-url", url, "bootstrap"]) == 2


def test_conversation_read_retains_explicit_client_scope(cli, monkeypatch, capsys):
    def request(req, timeout):
        assert req.method == "GET"
        assert req.full_url == "http://127.0.0.1:7860/api/interactions/conversations/conv-1?client_id=client-2"
        return Reply(b'{"id":"conv-1","client_id":"client-2","revision":7}')
    monkeypatch.setattr(cli, "open_request", request)
    assert cli.main(["conversation", "conv-1", "--client-id", "client-2"]) == 0
    assert json.loads(capsys.readouterr().out)["client_id"] == "client-2"


def test_edit_draft_sends_agent_origin_text_and_expected_revision(cli, monkeypatch):
    text = "你好，先整理咨询需求；$(echo secret) `whoami` 只是正文。"
    def request(req, timeout):
        assert req.method == "PATCH"
        assert req.full_url.endswith("/api/interactions/drafts/draft-1")
        assert json.loads(req.data) == {"client_id": "client-1", "expected_revision": 7,
                                        "text": text, "source": "agent"}
        return Reply(b'{"revision":8}')
    monkeypatch.setattr(cli, "open_request", request)
    assert cli.main(["edit-draft", "draft-1", "--client-id", "client-1", "--expected-revision", "7",
                     "--text", text]) == 0


def test_send_is_a_plan_until_exec_is_explicit(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli, "open_request", lambda *_a, **_k: pytest.fail("dry run must not send or read messages"))
    assert cli.main(["send-draft", "conv-1", "--draft-id", "draft-1", "--client-id", "client-1",
                     "--expected-revision", "7", "--operation-id", "reply-one"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["execute"] is False
    assert plan["client_id"] == "client-1"
    assert plan["conversation_id"] == "conv-1"


def test_exec_send_verifies_the_selected_draft_context_and_preserves_operation_id(cli, monkeypatch):
    calls = []
    def request(req, timeout):
        calls.append(req)
        if req.method == "GET":
            assert req.full_url.endswith("/conversations/conv-1?client_id=client-1")
            return Reply(b'{"id":"conv-1","client_id":"client-1","revision":7,"drafts":[{"id":"draft-1","status":"approved","text":"already approved"}]}')
        assert req.method == "POST"
        assert req.full_url.endswith("/drafts/draft-1/send")
        assert json.loads(req.data) == {"client_id": "client-1", "expected_revision": 7, "operation_id": "reply-one"}
        return Reply(b'{"id":"conv-1","revision":8,"sends":[{"mode":"simulation","status":"sent"}]}')
    monkeypatch.setattr(cli, "open_request", request)
    assert cli.main(["send-draft", "conv-1", "--draft-id", "draft-1", "--client-id", "client-1",
                     "--expected-revision", "7", "--operation-id", "reply-one", "--exec"]) == 0
    assert len(calls) == 2


@pytest.mark.parametrize("detail", [
    {"id": "conv-1", "client_id": "client-1", "revision": 8, "drafts": [{"id": "draft-1", "status": "approved"}]},
    {"id": "conv-1", "client_id": "client-1", "revision": 7, "drafts": [{"id": "different", "status": "approved"}]},
    {"id": "conv-1", "client_id": "client-1", "revision": 7, "drafts": [{"id": "draft-1", "status": "draft"}]},
    {"id": "conv-other", "client_id": "client-1", "revision": 7, "drafts": [{"id": "draft-1", "status": "approved"}]},
])
def test_exec_send_does_not_dispatch_changed_unapproved_or_mismatched_context(cli, monkeypatch, detail):
    def request(req, timeout):
        assert req.method == "GET", "invalid context must never be sent"
        return Reply(json.dumps(detail).encode())
    monkeypatch.setattr(cli, "open_request", request)
    assert cli.main(["send-draft", "conv-1", "--draft-id", "draft-1", "--client-id", "client-1",
                     "--expected-revision", "7", "--operation-id", "reply-one", "--exec"]) == 2


def test_conflicts_are_not_retried_and_private_server_bodies_are_not_printed(cli, monkeypatch, capsys):
    calls = []
    def request(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(b'{"detail":"PRIVATE-UPSTREAM"}'))
    monkeypatch.setattr(cli, "open_request", request)
    assert cli.main(["generate-draft", "conv-1", "--client-id", "client-1", "--expected-revision", "7"]) == 4
    assert len(calls) == 1
    assert "PRIVATE-UPSTREAM" not in capsys.readouterr().err


def test_knowledge_opt_in_and_rule_changes_are_explicit_and_scoped(cli, monkeypatch):
    calls = []
    def request(req, timeout):
        calls.append(req)
        return Reply(b'{"client_id":"client-1"}')
    monkeypatch.setattr(cli, "open_request", request)
    assert cli.main(["knowledge-add", "--client-id", "client-1", "--question", "了解课程",
                     "--keyword", "课程", "--answer", "请描述想了解的方向。", "--auto-send"]) == 0
    assert json.loads(calls[-1].data) == {"client_id": "client-1", "question": "了解课程",
        "keywords": ["课程"], "answer": "请描述想了解的方向。", "auto_send": True}
    assert cli.main(["set-rules", "--client-id", "client-1", "--mode", "auto_faq", "--enabled"]) == 0
    assert calls[-1].method == "PUT"
    assert json.loads(calls[-1].data) == {"client_id": "client-1", "mode": "auto_faq", "enabled": True}


def test_invalid_identifiers_do_not_reach_the_server(cli, monkeypatch):
    monkeypatch.setattr(cli, "open_request", lambda *_a, **_k: pytest.fail("must not connect"))
    assert cli.main(["conversation", "../conv-1", "--client-id", "client-1"]) == 2


def test_access_bearing_source_urls_are_redacted_in_cli_output(cli, monkeypatch, capsys):
    value = {"channels": [{"access_token": "PRIVATE-API-TOKEN", "client_token": "PRIVATE-CLIENT-TOKEN",
        "source_url": "https://www.xiaohongshu.com/explore/note?xsec_token=PRIVATE-LINK-TOKEN&xsec_source=pc_note"}]}
    monkeypatch.setattr(cli, "open_request", lambda *_a, **_k: Reply(json.dumps(value).encode()))
    assert cli.main(["bootstrap"]) == 0
    text = capsys.readouterr().out
    assert "PRIVATE-API-TOKEN" not in text
    assert "PRIVATE-CLIENT-TOKEN" not in text
    assert "PRIVATE-LINK-TOKEN" not in text
    assert "pc_note" in text


def test_nondefault_project_keeps_conversation_reads_inside_its_route(cli, monkeypatch):
    def request(req, timeout):
        assert req.method == "GET"
        assert req.full_url == "http://127.0.0.1:7860/api/projects/0123456789abcdef/interactions/conversations/conv-1?client_id=client-1"
        return Reply(b'{"id":"conv-1","client_id":"client-1","revision":1}')
    monkeypatch.setattr(cli, "open_request", request)
    assert cli.main(["--project-id", "0123456789abcdef", "conversation", "conv-1", "--client-id", "client-1"]) == 0


@pytest.mark.parametrize("project", ["../default", "project-a", "0123456789abcdeg", "0123456789ABCDEF"])
def test_invalid_project_scope_is_rejected_before_request(cli, monkeypatch, project):
    monkeypatch.setattr(cli, "open_request", lambda *_a, **_k: pytest.fail("must not connect"))
    assert cli.main(["--project-id", project, "bootstrap"]) == 2


def test_cli_round_trip_uses_real_routes_and_persists_only_a_simulation_receipt(cli, tmp_path, monkeypatch, capsys):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.interaction_routes import create_router

    app = FastAPI()
    app.include_router(create_router(lambda: tmp_path))
    with TestClient(app) as client:
        def open_request(req, timeout):
            from urllib.parse import urlsplit
            url = urlsplit(req.full_url)
            path = url.path + ("?" + url.query if url.query else "")
            data = json.loads(req.data) if req.data else None
            response = client.request(req.method, path, json=data)
            if response.status_code >= 400:
                raise urllib.error.HTTPError(req.full_url, response.status_code, "request failed", {}, io.BytesIO(response.content))
            return Reply(response.content)
        monkeypatch.setattr(cli, "open_request", open_request)
        assert cli.main(["bootstrap"]) == 0
        client_id = json.loads(capsys.readouterr().out)["client_id"]
        assert cli.main(["simulation", "--client-id", client_id]) == 0
        bootstrap = json.loads(capsys.readouterr().out)
        conversation_id = bootstrap["conversations"][0]["id"]
        assert cli.main(["conversation", conversation_id, "--client-id", client_id]) == 0
        detail = json.loads(capsys.readouterr().out)
        assert cli.main(["generate-draft", conversation_id, "--client-id", client_id,
                         "--expected-revision", str(detail["revision"])]) == 0
        detail = json.loads(capsys.readouterr().out)
        draft_id = detail["drafts"][-1]["id"]
        assert cli.main(["edit-draft", draft_id, "--client-id", client_id,
                         "--expected-revision", str(detail["revision"]), "--text", "模拟回复：具体课程由工作人员确认。"]) == 0
        detail = json.loads(capsys.readouterr().out)
        assert detail["drafts"][-1]["source"] == "agent"
        assert cli.main(["approve-draft", draft_id, "--client-id", client_id,
                         "--expected-revision", str(detail["revision"])]) == 0
        detail = json.loads(capsys.readouterr().out)
        assert cli.main(["send-draft", conversation_id, "--draft-id", draft_id, "--client-id", client_id,
                         "--expected-revision", str(detail["revision"]), "--operation-id", "test-simulation-one", "--exec"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "handled"
        assert result["sends"][-1]["mode"] == "simulation"
        assert result["sends"][-1]["status"] == "sent"
