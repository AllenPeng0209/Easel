"""Import an explicitly selected local asset into an owned BeefTV canvas.

Uses the public resource/asset APIs and the same operation layer as MCP. It
never submits generation and never prints the workspace client credential.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
import subprocess

import httpx

from easel.beeftv_bridge import client_for_workspace
from easel.beeftv_runtime import runtime_paths

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def runtime_client():
    paths = runtime_paths(PROJECT_ROOT)
    return client_for_workspace(PROJECT_ROOT, paths.workspace)


def import_media(path: str | Path, title: str, canvas_id: str, node_title: str | None = None) -> dict:
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("The selected media must be a regular file")
    mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    kind = mime.split("/", 1)[0]
    if kind not in ("image", "video", "audio"):
        raise ValueError("Only image, video and audio imports are supported")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    selected = runtime_client()
    client = httpx.Client(base_url=selected.base_url, trust_env=False, follow_redirects=False, timeout=90,
        headers=selected.headers,
        event_hooks={'request': [lambda _request: selected.before_request() if selected.before_request else None]})

    def api(method, endpoint, data=None, operation_id=None):
        response = client.request(method, endpoint, json=data,
            headers={"X-Idempotency-Key": operation_id} if operation_id else {})
        payload = response.json()
        if not response.is_success or payload.get("code") != 0:
            raise RuntimeError(f"{endpoint}: {payload.get('reason') or payload.get('code')}: {payload.get('msg')}")
        return payload["data"]

    def op(name, params, operation_id=None):
        body = {"params": params}
        if operation_id:
            body["opId"] = operation_id
        return api("POST", "/ops/" + name, body)["result"]

    try:
        metadata = {}
        if kind in ("video", "audio"):
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,width,height", "-of", "json", str(source)],
                check=True, capture_output=True, text=True)
            info = json.loads(probe.stdout)
            metadata["durationMs"] = round(float(info.get("format", {}).get("duration", 0)) * 1000)
            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video":
                    metadata.update(width=stream["width"], height=stream["height"])
        elif kind == "image":
            from PIL import Image
            with Image.open(source) as image:
                metadata.update(width=image.width, height=image.height)
        with source.open("rb") as file:
            response = client.post("/resources", files={"file": (source.name, file, mime)},
                data={"kind": kind, **{k: str(v) for k, v in metadata.items()}},
                headers={"X-Idempotency-Key": "easel-media-" + digest})
        payload = response.json()
        if not response.is_success or payload.get("code") != 0:
            raise RuntimeError("Resource upload failed: " + str(payload.get("msg")))
        resource = payload["data"]["resource"]
        resource_id = resource["id"]
        asset_id = "easel-asset-" + digest[:24]
        existing = client.get("/assets/" + asset_id)
        if existing.status_code == 404:
            url = "/api/resources/" + resource_id + "/file"
            asset = {"id": asset_id, "kind": kind, "title": title, "tags": [], "coverUrl": url,
                "data": {"dataUrl" if kind == "image" else "url": url,
                         "storageKey": "resource:" + resource_id, "bytes": source.stat().st_size,
                         "mimeType": mime, **metadata}}
            api("PUT", "/assets/" + asset_id, {"asset": asset}, "easel-asset-" + digest)
        elif not existing.is_success:
            raise RuntimeError("Cannot establish existing asset state")
        canvas = op("canvas.get", {"canvasId": canvas_id})["canvas"]
        node = next((n for n in canvas["nodes"] if n.get("type") == kind and
                     n.get("metadata", {}).get("assetId") == asset_id), None)
        if node is not None:
            return {"canvasId": canvas_id, "nodeId": node["id"], "assetId": asset_id,
                    "resourceId": resource_id, "revision": canvas["revision"], "reused": True}
        name = node_title or title
        node = next((n for n in canvas["nodes"] if n.get("type") == kind and
                     n.get("title") == name and not n.get("metadata", {}).get("assetId") and
                     not n.get("metadata", {}).get("taskId")), None)
        if node is None:
            identity = hashlib.sha256((canvas_id + digest + name).encode()).hexdigest()[:32]
            created = op("canvas.nodes.create", {"canvasId": canvas_id,
                "expectedRevision": canvas["revision"], "nodes": [{"type": kind, "title": name}]},
                "easel-media-node-" + identity)
            node_id = created["created"][0]["id"]
            revision = created["revision"]
        else:
            node_id, revision = node["id"], canvas["revision"]
        bound = op("canvas.node.bind_asset", {"canvasId": canvas_id, "nodeId": node_id,
            "assetId": asset_id, "expectedRevision": revision}, "easel-media-bind-" + node_id + "-" + digest[:16])
        return {"canvasId": canvas_id, "nodeId": node_id, "assetId": asset_id,
                "resourceId": resource_id, "revision": bound["revision"], "reused": False}
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--canvas-id", required=True)
    parser.add_argument("--node-title")
    arguments = parser.parse_args()
    print(json.dumps(import_media(arguments.file, arguments.title, arguments.canvas_id,
                                 arguments.node_title), ensure_ascii=False))
