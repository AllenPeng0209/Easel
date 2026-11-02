#!/usr/bin/env python3
"""Operate Easel's scoped interaction inbox without platform credentials in the CLI."""
from __future__ import annotations

import argparse
from functools import partial
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


class InputError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_request(request, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)


def local_base(value: str) -> str:
    try:
        url = urllib.parse.urlsplit(value)
        valid = (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
                 and not url.username and not url.password and not url.query and not url.fragment
                 and not url.path.strip("/") and url.port != 0
                 and not any(ord(char) < 33 for char in value))
    except ValueError:
        valid = False
    if not valid:
        raise InputError("base-url must be a local HTTP origin without credentials or query parameters")
    return value.rstrip("/")


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value):
        raise InputError("invalid client, conversation or draft identifier")
    return value


def project_identifier(value: str) -> str:
    if value != "default" and not re.fullmatch(r"[a-f0-9]{16}", value):
        raise InputError("project-id must be default or the actual 16-character project identifier")
    return value


def operation_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
        raise InputError("operation-id must contain 1–128 letters, digits, underscores, colons or hyphens")
    return value


def positive(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def request(base: str, path: str, *, method="GET", payload=None, project_id="default"):
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    project = project_identifier(project_id)
    root = "/api/interactions" if project == "default" else f"/api/projects/{project}/interactions"
    req = urllib.request.Request(base + root + path, data=raw, method=method,
        headers={"Content-Type": "application/json", "Origin": base})
    with open_request(req, timeout=45) as response:
        result = json.loads(response.read())
    if not isinstance(result, (dict, list)):
        raise InputError("Easel returned an invalid interaction response")
    return result


def safe_output(value):
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if re.search(r"token|api.?key|authorization|cookie|password|secret", key, re.I)
                      else safe_output(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_output(item) for item in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        try:
            url = urllib.parse.urlsplit(value)
            query = urllib.parse.parse_qsl(url.query, keep_blank_values=True)
            query = [(key, "[REDACTED]" if re.search(r"token|key|secret|signature|password", key, re.I) else item)
                     for key, item in query]
            host = url.netloc.rsplit("@", 1)[-1]
            fragment = "" if re.search(r"token|key|secret|password", url.fragment, re.I) else url.fragment
            return urllib.parse.urlunsplit((url.scheme, host, url.path, urllib.parse.urlencode(query), fragment))
        except ValueError:
            return "[invalid source URL]"
    return value


def emit(value):
    print(json.dumps(safe_output(value), ensure_ascii=False, indent=2))
    return 0


def scoped(client_id: str) -> str:
    return "?" + urllib.parse.urlencode({"client_id": identifier(client_id)})


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default=os.environ.get("EASEL_INTERACTIONS_BASE_URL", "http://127.0.0.1:7860"))
    p.add_argument("--project-id", default="default", help="Select the actual app project; default preserves the original workspace")
    commands = p.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap", help="Read clients, channels, inbox, knowledge and current rules")
    bootstrap.add_argument("--client-id")
    create_client = commands.add_parser("create-client", help="Create a separately scoped client workspace")
    create_client.add_argument("--name", required=True)
    for name in ("simulation", "knowledge-list", "rules"):
        sub = commands.add_parser(name)
        sub.add_argument("--client-id", required=True)
    conversation = commands.add_parser("conversation", help="Read one conversation in its client scope")
    conversation.add_argument("conversation_id")
    conversation.add_argument("--client-id", required=True)
    for name in ("generate-draft", "action"):
        sub = commands.add_parser(name)
        sub.add_argument("conversation_id")
        sub.add_argument("--client-id", required=True)
        sub.add_argument("--expected-revision", required=True, type=positive)
        if name == "action":
            sub.add_argument("--action", choices=("takeover", "resolve", "reopen"), required=True)
    for name in ("edit-draft", "approve-draft"):
        sub = commands.add_parser(name)
        sub.add_argument("draft_id")
        sub.add_argument("--client-id", required=True)
        sub.add_argument("--expected-revision", required=True, type=positive)
        if name == "edit-draft":
            text = sub.add_mutually_exclusive_group(required=True)
            text.add_argument("--text")
            text.add_argument("--text-file")
            sub.add_argument("--source", choices=("agent", "human"), default="agent")
        else:
            sub.add_argument("--review-kind", choices=("agent", "human", "simulated"), default="agent")
    send = commands.add_parser("send-draft", help="Plan a send; --exec verifies context then dispatches the approved draft")
    send.add_argument("conversation_id")
    send.add_argument("--draft-id", required=True)
    send.add_argument("--client-id", required=True)
    send.add_argument("--expected-revision", type=positive, required=True)
    send.add_argument("--operation-id", required=True)
    send.add_argument("--exec", action="store_true")
    knowledge = commands.add_parser("knowledge-add", help="Save a grounded FAQ; auto-send is an explicit opt-in")
    knowledge.add_argument("--client-id", required=True)
    knowledge.add_argument("--question", required=True)
    knowledge.add_argument("--keyword", action="append", required=True)
    answer = knowledge.add_mutually_exclusive_group(required=True)
    answer.add_argument("--answer")
    answer.add_argument("--answer-file")
    knowledge.add_argument("--auto-send", action="store_true")
    rules = commands.add_parser("set-rules", help="Change one client's reply mode and enablement explicitly")
    rules.add_argument("--client-id", required=True)
    rules.add_argument("--mode", choices=("manual", "assist", "auto_faq"), required=True)
    enabled = rules.add_mutually_exclusive_group(required=True)
    enabled.add_argument("--enabled", action="store_true")
    enabled.add_argument("--disabled", action="store_true")
    rules.add_argument("--blocked-keyword", action="append")
    run = commands.add_parser("automation-run", help="Plan a scoped automation pass; --exec applies the current rules")
    run.add_argument("--client-id", required=True)
    run.add_argument("--exec", action="store_true")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        base = local_base(args.base_url)
        project = project_identifier(args.project_id)
        call = partial(request, base, project_id=project)
        if args.command == "bootstrap":
            return emit(call("/bootstrap" + (scoped(args.client_id) if args.client_id else "")))
        if args.command == "create-client":
            return emit(call("/clients", method="POST", payload={"name": args.name}))
        client_id = identifier(args.client_id)
        data = {"client_id": client_id}
        if args.command in {"knowledge-list", "rules"}:
            result = call("/bootstrap" + scoped(client_id))
            return emit(result["knowledge" if args.command == "knowledge-list" else "rules"])
        if args.command == "simulation":
            return emit(call("/simulation", method="POST", payload=data))
        if args.command == "knowledge-add":
            answer = Path(args.answer_file).read_text(encoding="utf-8") if args.answer_file else args.answer
            data.update(question=args.question, keywords=args.keyword, answer=answer, auto_send=args.auto_send)
            return emit(call("/knowledge", method="POST", payload=data))
        if args.command == "set-rules":
            data.update(mode=args.mode, enabled=args.enabled)
            if args.blocked_keyword is not None:
                data["blocked_keywords"] = args.blocked_keyword
            return emit(call("/rules", method="PUT", payload=data))
        if args.command == "automation-run":
            if not args.exec:
                return emit({"action": "automation-run", **data, "project_id": project, "execute": False})
            return emit(call("/automation/run", method="POST", payload=data))
        if args.command == "conversation":
            return emit(call("/conversations/" + identifier(args.conversation_id) + scoped(client_id)))
        data["expected_revision"] = args.expected_revision
        if args.command in {"generate-draft", "action"}:
            path = "/conversations/" + identifier(args.conversation_id)
            if args.command == "generate-draft":
                return emit(call(path + "/draft", method="POST", payload=data))
            data["action"] = args.action
            return emit(call(path + "/action", method="POST", payload=data))
        draft_id = identifier(args.draft_id)
        path = "/drafts/" + draft_id
        if args.command == "edit-draft":
            text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else args.text
            if not text or not text.strip():
                raise InputError("draft text must not be empty")
            data.update(text=text, source=args.source)
            return emit(call(path, method="PATCH", payload=data))
        if args.command == "approve-draft":
            data["review_kind"] = args.review_kind
            return emit(call(path + "/approve", method="POST", payload=data))
        conversation_id = identifier(args.conversation_id)
        data["operation_id"] = operation_id(args.operation_id)
        if not args.exec:
            return emit({"action": "send-draft", **data, "project_id": project, "conversation_id": conversation_id,
                         "draft_id": draft_id, "execute": False})
        detail = call("/conversations/" + conversation_id + scoped(client_id))
        drafts = detail.get("drafts", [])
        current = drafts[-1] if drafts else {}
        if (detail.get("id") != conversation_id or detail.get("client_id") != client_id
                or detail.get("revision") != args.expected_revision or current.get("id") != draft_id
                or current.get("status") != "approved"):
            raise InputError("draft context changed or is not approved; read the conversation before sending")
        return emit(call(path + "/send", method="POST", payload=data))
    except urllib.error.HTTPError as error:
        print(f"Easel HTTP {error.code}; no retry was sent", file=sys.stderr)
        return {404: 3, 409: 4, 401: 6, 403: 6, 422: 8}.get(error.code, 1)
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, TypeError):
        print("Interaction request could not be completed; check the selected scope, inputs and local service", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
