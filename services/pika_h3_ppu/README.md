# Private Pika H3 PPU gateway

This directory is a model-free Python service. It calls the operator-owned
official Pika CLI once per job, with its native audio, and never runs TTS.
Python 3.10+, Pillow, FFmpeg and FFprobe are required. Model/runtime dependencies
remain in the isolated Pika venv. The API is loopback-only and requires Bearer
authentication on **every endpoint**, including health and content.

## Operator launch

Copy this directory, preserving the `services/pika_h3_ppu/` package layout,
under an operator-owned private directory, for example `OLDROOT/gateway-code/`.
`PYTHONPATH` must point to that directory. Do not copy SQLite state into another
service or share a writable state directory. The token file must be owned by the
service user, mode 0600, and contain at least 32 token characters. Generate a
new hex token without printing it, or use the already provisioned dedicated
Pika token; never reuse an unrelated account credential.

The following is a launch template; choose the physical device only after the
benchmark has finished and the old resident model has been drained. The service
will independently acquire both device locks and check at least 84 GiB free
using the same venv/vendor SDK immediately before every real CLI launch.

```bash
PIKA_ROOT=/mnt/data/workdir/yanlun/services/pika_h3_flex_0711_20261105
PYTHONPATH="$PIKA_ROOT/gateway-code" \
  "$PIKA_ROOT/venv/bin/python" -m services.pika_h3_ppu.gateway \
  --runtime-root "$PIKA_ROOT" \
  --source-manifest "$PIKA_ROOT/code-source-manifest.json" \
  --runner-sha256 6fa8ecac0f26533b13ef119b9d511e6a83b0427e1fd535f3602b69a744f6f1a7 \
  --state-root "$PIKA_ROOT/gateway-state" \
  --token-file "$PIKA_ROOT/gateway-token" \
  --device 0 --host 127.0.0.1 --port 8882 --max-pending 8
```

The checked-in recipe now invokes `venv/bin/python -m pika_h3.cli` through its
`run.sh`; the pinned runner SHA above must match the remote file. The official
source manifest is either `{source_commit, files: {relative_path: sha256}}` or
the deployed `{version, repo, commit, files: [{path,size,sha256}]}` format.
The current 61-file deployed manifest SHA is
`7c22034ab8e4fa32d82123c8727dc8b67a305bf0e4d429de568f4aaeb1cbb515`.
The service checks every listed file and refuses extra Python source before
each invocation. It also probes that the venv imports `pika_h3` from this root.

Use `--tested-mode t2va:480p:16:9:5s` only after the operator has verified that
exact mode. Multiple labels are allowed. Empty means **not yet tested**; neither
`/health` nor `/v1/models` claims that weights are resident or model readiness
was probed. This is a fresh-process, load/generate/unload service.

Expose it to Easel only through an authenticated private SSH tunnel. The
local Easel base URL is `http://127.0.0.1:8882`; model ID is
`pika-h3-flex-6step`. Do not change an old Ref11 model ID to label it Pika.
Do not start a gateway generation while the five-second benchmark owns its
device lock. This package does not remotely deploy itself or change Easel
configuration.

## API contract

`POST /v1/videos` accepts only:

```json
{
  "model": "pika-h3-flex-6step",
  "prompt": "A cat looks at the camera and meows naturally.",
  "seconds": 5,
  "size": "16:9",
  "resolution": "480p",
  "seed": 42,
  "request_id": "easel-pika-example-001",
  "images": []
}
```

- `prompt`: 1–8000 characters; `seconds` integer 5–15, default 5. `duration`
  is an alias, but supplying both is invalid. Seed integer 0 through 2^63−1,
  default 0. Size `16:9` or `9:16`; resolution `480p` or `768p`.
- `images`: empty or exactly one `{data_url: "data:image/png;base64,..."}` or
  JPEG. The MIME must match full Pillow-decoded bytes; max 10 MiB, 16–8192
  pixels per side, max 32 MP. No animation, file paths or remote URL fetching.
  Nonempty audio/video references, legacy `image`, unknown fields, steps,
  commands and caller-selected devices are rejected.
- Same `request_id` and canonical input returns the original job. Different
  input with the same ID returns 409. Easel must always supply a stable ID.
  Omitting it intentionally creates a new job, and is not a retry mechanism.

`GET /v1/videos/{id}` and `/v1/videos/{id}/status` return `queued`,
`in_progress`, `completed`, `failed` or `unknown`. Completed results contain
`video_url: "/v1/videos/{id}/content"` and a validation receipt. Fetch content
with the same Bearer header. `/v1/videos` lists at most 100 minimal records;
`/v1/models` lists the single Pika model. Status/list responses contain no
prompt, caller input base64, token, or absolute output paths.

Actual geometry is 832×480 / 480×832 for 480p and 1344×768 / 768×1344 for 768p,
24 fps. Frames align upward to `17n+5`: a requested five-second video has
124 frames (about 5.167 seconds). Native audio must be AAC, 32 kHz stereo.
Completed means pinned adapter/base/source/runner/six-step provenance and
geometry were checked and the **whole video and audio decoded successfully**.
This is structural acceptance, not a human speech/continuity quality review.
The reported `sampling_seconds` includes transfers and synchronization;
pure DiT latency is unmeasured.

## Restart and shutdown

State, output, prompts and private diagnostic files are confined to the 0700
state root; files are 0600. Existing non-private state roots and symlinks are
refused. A service lease prevents two instances using the same state root.
Intent is committed before launching; device lock FDs remain with the child.
The original PID/start identity is stored for operator audit.

On restart, pending/in-progress jobs become `unknown` and are **never
automatically relaunched**. Only the original complete output, original
execution attestation and native metadata can be recovered as completed.
Recovery decoding uses a separate bounded CPU validator and does not block
queries. Failed and unknown jobs remain the same job on same-ID submissions.
Private `failure.json` reports controlled phase/error type; worker logs remain
private. No token or base64 is placed in audit receipts.

SIGTERM/SIGINT begins draining: new IDs are rejected, original idempotent
submissions and queries remain available, queued jobs become unknown, and
the one active CLI is allowed to finish before HTTP shutdown. There is no
automatic timeout, retry or kill. A second invocation is a fresh process,
not a resident-model warm benchmark. The benchmark's two-invocation limit
is not a production API quota.

## Offline verification

```bash
python3 -m pytest --noconftest tests/test_pika_h3_gateway.py -q
ruff check services/pika_h3_ppu tests/test_pika_h3_gateway.py
```

The suite uses fake execution and a tiny real FFmpeg AV fixture, never PPU or
model weights. `--noconftest` avoids unrelated project-wide Web authentication
fixtures; the gateway's actual Bearer/HTTP boundary is tested directly.
