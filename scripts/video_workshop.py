#!/usr/bin/env python3
"""Local Easel project commands and an argv-only bridge to the official BeefTV CLI."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = ROOT / "scripts" / "beeftv_runtime.sh"


class InputError(ValueError):
    pass


class NativeError(Exception):
    def __init__(self, code: int):
        super().__init__(f"BeefTV discovery failed (exit {code}); inspect the local runtime")
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_request(request, timeout):
    # Loopback operations must not leave through inherited proxy settings or redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)


def local_base(value: str) -> str:
    try:
        url = urllib.parse.urlsplit(value)
        valid = (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
                 and not url.username and not url.password and not url.query and not url.fragment
                 and not url.path.strip("/") and url.port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise InputError("base-url must be a local HTTP origin without credentials or query parameters")
    return value.rstrip("/")


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value):
        raise InputError("invalid project identifier")
    return value


def relative_media(value: str) -> str:
    path = PurePosixPath(value)
    if (path.is_absolute() or "\\" in value or not path.parts
            or any(part.startswith((".", "_")) for part in path.parts)
            or path.suffix.lower() not in {".mp4", ".mov", ".webm", ".m4v", ".mkv"}):
        raise InputError("path must name a video relative to outputs, outside hidden or system directories")
    return str(path)


def easel_request(base: str, path: str, payload=None):
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/workshop" + path, data=raw,
        headers={"Content-Type": "application/json", "Origin": base},
        method="GET" if payload is None else "POST",
    )
    with open_request(request, timeout=20) as response:
        result = json.loads(response.read())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def runtime_command(*args: str) -> list[str]:
    script = Path(os.environ.get("EASEL_BEEFTV_RUNTIME_SCRIPT", str(DEFAULT_RUNTIME))).expanduser()
    if not script.is_file():
        raise InputError("BeefTV runtime script is unavailable; install the local runtime first")
    return ["bash", str(script), *args]


def catalogue(mode: str) -> list[dict]:
    args = ("cli", "business", "discover") if mode == "business" else ("cli", "ops", "--json")
    result = subprocess.run(runtime_command(*args), capture_output=True, text=True, check=False)
    if result.returncode:
        raise NativeError(result.returncode)
    try:
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = data.get("ops", data.get("tools"))
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError
    except (ValueError, TypeError):
        raise InputError("BeefTV returned an invalid operation catalogue") from None
    return data


def params_text(args) -> str:
    text = Path(args.params_file).read_text(encoding="utf-8") if args.params_file else args.params
    if not isinstance(json.loads(text), dict):
        raise InputError("params must be a JSON object")
    return text


def native_call(args, mode: str) -> int:
    params = params_text(args)
    operation = next((item for item in catalogue(mode) if item.get("id") == args.operation), None)
    if operation is None:
        raise InputError("unknown operation; discover the current runtime catalogue first")
    if operation.get("readOnly") is not True and not args.operation_id:
        raise InputError("write operations require --operation-id; reuse that ID only for the same request")
    if args.operation_id and (not args.operation_id.strip() or any(ord(c) < 32 for c in args.operation_id)):
        raise InputError("operation-id must be nonempty and contain no control characters")
    command = (["cli", "business", "call", "--tool", args.operation, "--params", params]
               if mode == "business" else
               ["cli", "ops", "call", args.operation, "--params", params, "--json"])
    if args.operation_id:
        command += ["--operation-id", args.operation_id]
    return subprocess.run(runtime_command(*command), check=False).returncode


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default=os.environ.get("EASEL_WORKSHOP_BASE_URL", "http://127.0.0.1:7860"))
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Read Easel's actual workshop runtime status")
    commands.add_parser("projects", help="List saved Easel workshop projects")
    create = commands.add_parser("create", help="Create a project and link its real BeefTV canvas")
    create.add_argument("--title", required=True)
    create.add_argument("--brief", required=True)
    create.add_argument("--reference-url", default="")
    create.add_argument("--aspect-ratio", choices=("9:16", "16:9", "1:1", "4:5"), default="9:16")
    get = commands.add_parser("project", help="Read a project with current revision and deliverables")
    get.add_argument("project_id")
    register = commands.add_parser("register-delivery", help="Register an existing outputs-relative video")
    register.add_argument("project_id")
    register.add_argument("--expected-revision", type=positive, required=True)
    register.add_argument("--engine-revision", type=nonnegative,
                          help="actual sourceRevision from the BeefTV render receipt; never inferred")
    register.add_argument("--path", required=True)
    for name in ("comment", "review"):
        review = commands.add_parser(name)
        review.add_argument("project_id")
        review.add_argument("--expected-revision", type=positive, required=True)
        review.add_argument("--kind", choices=("human", "simulated"), required=True)
        review.add_argument("--notes", required=True)
        if name == "comment":
            review.add_argument("--at-seconds", type=float, required=True)
        else:
            review.add_argument("--artifact-id", required=True)
            review.add_argument("--decision", choices=("approved", "changes_requested"), required=True)
    for name in ("business-list", "operations"):
        discovery = commands.add_parser(name, help="Read the official current BeefTV capability catalogue")
        discovery.add_argument("--query", default="")
    for name in ("business-call", "operation-call"):
        call = commands.add_parser(name, help="Call one discovered official BeefTV capability, without retries")
        call.add_argument("operation")
        params = call.add_mutually_exclusive_group()
        params.add_argument("--params", default="{}")
        params.add_argument("--params-file")
        call.add_argument("--operation-id")
    mcp = commands.add_parser("mcp", help="Pass stdio directly to the official BeefTV MCP server")
    mcp.add_argument("--read-only", action="store_true")
    mcp.add_argument("--all-business-tools", action="store_true")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "mcp":
            flags = [flag for enabled, flag in ((args.read_only, "--read-only"),
                     (args.all_business_tools, "--all-business-tools")) if enabled]
            # No output on this path: stdout belongs to the official MCP protocol.
            return subprocess.run(runtime_command("mcp", *flags), check=False).returncode
        if args.command in ("business-list", "operations"):
            items = catalogue("business" if args.command == "business-list" else "operations")
            query = args.query.casefold()
            if query:
                items = [item for item in items if query in json.dumps(item, ensure_ascii=False).casefold()]
            print(json.dumps(items, ensure_ascii=False, indent=2))
            return 0
        if args.command in ("business-call", "operation-call"):
            return native_call(args, "business" if args.command == "business-call" else "operations")
        base = local_base(args.base_url)
        if args.command == "status":
            return easel_request(base, "/status")
        if args.command == "projects":
            return easel_request(base, "/projects")
        if args.command == "create":
            return easel_request(base, "/projects", {
                "title": args.title, "brief": args.brief,
                "reference_url": args.reference_url, "aspect_ratio": args.aspect_ratio,
            })
        project = identifier(args.project_id)
        path = "/projects/" + project
        if args.command == "project":
            return easel_request(base, path)
        data = {"expected_revision": args.expected_revision}
        if args.command == "register-delivery":
            data["path"] = relative_media(args.path)
            if args.engine_revision is not None:
                data["engine_revision"] = args.engine_revision
            return easel_request(base, path + "/deliverables", data)
        data.update(kind=args.kind, notes=args.notes)
        if args.command == "comment":
            if not args.at_seconds >= 0 or not args.at_seconds < float("inf"):
                raise InputError("at-seconds must be finite and nonnegative")
            data["at_seconds"] = args.at_seconds
            return easel_request(base, path + "/comments", data)
        data.update(artifact_id=args.artifact_id, decision=args.decision)
        return easel_request(base, path + "/reviews", data)
    except urllib.error.HTTPError as error:
        print(f"Easel HTTP {error.code}; no retry was sent", file=sys.stderr)
        return {404: 3, 409: 4, 401: 6, 403: 6, 422: 8}.get(error.code, 1)
    except NativeError as error:
        print(str(error), file=sys.stderr)
        return error.code
    except (InputError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, urllib.error.URLError):
        print("Local workshop connection or file operation failed", file=sys.stderr)
        return 9


if __name__ == "__main__":
    raise SystemExit(main())
