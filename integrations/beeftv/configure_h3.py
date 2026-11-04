"""Install the local PPU protocol and configure an isolated BeefTV channel.

No generation is submitted. Keys are read in memory from Easel's ignored .env
and sent only to the loopback workspace configuration API.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import zipfile

import httpx

from easel.beeftv_bridge import client_for_workspace
from easel.beeftv_runtime import runtime_paths

PROJECT = Path(__file__).resolve().parents[2]
SPECS = {
    "t2va": {"directory": "ppu-h3-json", "provider": "easel-ppu-h3-json",
             "channel": "easel-ppu-h3-local", "name": "PPU MiniMax H3 · 本机",
             "base": "http://127.0.0.1:8878", "model": "minimax-h3-dense8"},
    "ref11": {"directory": "ppu-h3-ref11-json", "provider": "easel-ppu-h3-ref11-json",
              "channel": "easel-ppu-h3-ref11-local", "name": "PPU MiniMax H3 Ref11 · 全参考",
              "base": "http://127.0.0.1:8881", "model": "minimax-h3-ref2va-ref11-8step"},
}


def runtime_client():
    paths = runtime_paths(PROJECT)
    return client_for_workspace(PROJECT, paths.workspace)


def existing_video_key() -> str:
    value = os.environ.get("VIDEO_API_KEY", "")
    if not value:
        for line in (PROJECT / ".env").read_text().splitlines():
            if line.strip().startswith("VIDEO_API_KEY="):
                value = line.split("=", 1)[1].strip().strip("\"'")
                break
    if not value:
        raise RuntimeError("VIDEO_API_KEY is not configured in the existing local environment")
    return value


def canonical_manifest_from_zip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return json.loads(archive.read("manifest.json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=tuple(SPECS), default="ref11")
    parser.add_argument("--check", action="store_true", help="read-only configuration verification")
    parser.add_argument("--refresh-key", action="store_true", help="explicitly replace the saved channel key from the current environment")
    args = parser.parse_args()
    spec = SPECS[args.mode]
    paths = runtime_paths(PROJECT)
    selected = runtime_client()
    expected = json.loads((Path(__file__).parent / spec["directory"] / "manifest.json").read_text())
    warnings = []
    with httpx.Client(base_url=selected.base_url, trust_env=False, follow_redirects=False,
        timeout=30, headers=selected.headers,
        event_hooks={'request': [lambda _request: selected.before_request() if selected.before_request else None]}) as client:
        def api(method, path, data=None, operation_id=None):
            response = client.request(method, path, json=data,
                headers={"X-Idempotency-Key": operation_id} if operation_id else {})
            envelope = response.json()
            if not response.is_success or envelope.get("code") != 0:
                raise RuntimeError(f"{path}: {envelope.get('reason') or envelope.get('code')}: {envelope.get('msg')}")
            return envelope["data"]

        package_response = client.get("/plugins/" + spec["provider"] + "/package")
        installed = package_response.is_success and canonical_manifest_from_zip(package_response.content) == expected
        if not installed and not args.check:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                entry = zipfile.ZipInfo("manifest.json", date_time=(2026, 1, 1, 0, 0, 0))
                archive.writestr(entry, json.dumps(expected, ensure_ascii=False, indent=2))
            response = client.post("/plugins", files={"file": (spec["provider"] + ".beeftv-plugin", buffer.getvalue(), "application/zip")},
                headers={"X-Idempotency-Key": "easel-install-" + spec["provider"] + "-" + expected["version"]})
            # v2.1.1 may persist the plugin and then fail its admin-audit write.
            # Establish exact persisted identity before proceeding, never retry
            # the uncertain write or pretend that its audit completed.
            verified = client.get("/plugins/" + spec["provider"] + "/package")
            installed = verified.is_success and canonical_manifest_from_zip(verified.content) == expected
            if not installed:
                raise RuntimeError("Protocol installation did not persist the exact requested manifest")
            if not response.is_success:
                warnings.append("Protocol persisted and verified, but upstream v2.1.1 reported an installation/audit error; the audit was not repaired")
        providers = api("GET", "/plugins/catalog?scope=user.custom-channel&capability=video")["providers"]
        enabled = any(p.get("id") == spec["provider"] and p.get("enabled") for p in providers)
        state = api("GET", "/workspace/model-config")
        config = state["config"]
        previous = next((c for c in config.get("channels", []) if c.get("id") == spec["channel"]), None)
        selector = spec["channel"] + "::" + spec["model"]
        configured = bool(previous and previous.get("hasApiKey") and previous.get("baseUrl") == spec["base"] and
            previous.get("interfaceType") == spec["provider"] and spec["model"] in previous.get("models", []))
        if args.check:
            print(json.dumps({"mode": args.mode, "installed": installed, "enabled": enabled,
                "configured": configured, "channel_id": spec["channel"], "model_selector": selector,
                "model_config_revision": state["revision"]}, ensure_ascii=False))
            raise SystemExit(0 if installed and enabled and configured else 1)
        if not configured or args.refresh_key:
            channel = {"id": spec["channel"], "name": spec["name"], "baseUrl": spec["base"],
                "apiKey": existing_video_key(), "apiFormat": "openai", "interfaceType": spec["provider"],
                "models": [spec["model"]], "scope": "user", "enabled": True, "concurrencyLimit": 1,
                "modelProfiles": [{"model": spec["model"], "displayName": spec["name"],
                    "capability": "video", "protocol": spec["provider"]}]}
            config["channels"] = [c for c in config.get("channels", []) if c.get("id") != spec["channel"]] + [channel]
            config.update(channelMode="remote", videoModel=selector, videoSeconds="6", videoGenerateAudio="true")
            saved = api("PUT", "/workspace/model-config", {"config": config, "expectedRevision": state["revision"]},
                        "easel-configure-" + spec["channel"] + "-" + str(state["revision"]))
            state["revision"] = saved["revision"]
            local_config = paths.data / "local-model-config.json"
            if local_config.exists():
                os.chmod(local_config, 0o600)
        print(json.dumps({"mode": args.mode, "installed": installed, "enabled": enabled,
            "configured": True, "channel_id": spec["channel"], "model_selector": selector,
            "model_config_revision": state["revision"], "warnings": warnings,
            "generation_submitted": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
