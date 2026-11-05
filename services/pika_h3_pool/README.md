# Pika H3 Flex queued PPU API

This API schedules the existing official Pika H3 Flex CLI on the user-selected
DSW instance `yanlun_1104`. It manages physical devices 0–15. Each model request
uses one PPU; this is a pool of independent single-card requests, not a 16-card
tensor-parallel implementation.

The deployed default is four concurrent jobs and up to 128 queued jobs. Excess
work waits in a persistent SQLite FIFO. The dispatcher observes free device
memory, acquires shared physical-device file locks, and reserves host memory
before loading CPU-offloaded weights. Busy or unobservable resources leave work
queued. API callers cannot choose devices, commands, filesystem paths or model
steps.

## Current private deployment

- Existing model root: `/mnt/data/workdir/yanlun/services/pika_h3_flex_20261105`.
- New service state/config/releases: the `api16/` subdirectory of that root.
- Remote API: `http://127.0.0.1:8882` on **this new instance only**.
- Current Mac private SSH tunnel: `http://127.0.0.1:18882`.
- Supervisor program: `pika-h3-api16`.
- Local private connection: `/Users/pengyanlun/workdir/Easel/.easel/pika-h3-api16/connection.json`.
- Local example client: `/Users/pengyanlun/workdir/Easel/outputs/pika-h3-api16-20261105/api-client.py`.

Every endpoint requires the dedicated Bearer credential. It is stored in owned
0600 files and is not printed in reports or embedded in model prompts. The
service binds only loopback; clients on other machines need a private SSH
tunnel and the credential through an authorized channel. No public DSW port or
security-group change was made. The old stopped Ref11 controller is separate.

## Calling the API

Create a JSON request file:

```json
{
  "model": "pika-h3-flex-6step",
  "prompt": "A small beige organizer tray on an oak table. A hand places a key inside. Natural room sound.",
  "seconds": 5,
  "size": "9:16",
  "resolution": "480p",
  "seed": 42,
  "request_id": "my-project-shot-001-v1"
}
```

An optional `images` array accepts exactly one inline PNG/JPEG `data_url` for
I2VA, up to 10 MiB. No remote URL fetching, caller-selected local paths or
audio/video references are admitted in this initial HTTP contract. The existing
official offline CLI has other modes; those are not silently exposed here.

```bash
python /Users/pengyanlun/workdir/Easel/outputs/pika-h3-api16-20261105/api-client.py health
python /Users/pengyanlun/workdir/Easel/outputs/pika-h3-api16-20261105/api-client.py submit /absolute/path/request.json
python /Users/pengyanlun/workdir/Easel/outputs/pika-h3-api16-20261105/api-client.py status video_ACTUAL_JOB_ID
python /Users/pengyanlun/workdir/Easel/outputs/pika-h3-api16-20261105/api-client.py queue
python /Users/pengyanlun/workdir/Easel/outputs/pika-h3-api16-20261105/api-client.py download video_ACTUAL_JOB_ID --output /absolute/path/result.mp4
```

The example client reads the local dedicated credential privately, checks the
download SHA256 and does not retry POST automatically. Replace the job placeholder
with the actual returned `video_` ID.

For another HTTP client, set `PIKA_H3_API_TOKEN` securely without printing it:

```bash
curl http://127.0.0.1:18882/v1/videos \
  -H "Authorization: Bearer $PIKA_H3_API_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @/absolute/path/request.json
```

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Authenticated service, queue and pool state |
| GET | `/v1/models` | Model, declared modes and specifically tested mode labels |
| POST | `/v1/videos` | Persist and enqueue a request |
| GET | `/v1/videos` | Up to 100 minimal job records |
| GET | `/v1/videos/{id}` | Original job status, device, queue position, result receipt |
| GET | `/v1/videos/{id}/content` | Original completed MP4; same Bearer auth |
| GET | `/v1/queue` | Counts, configured pool, concurrency and active devices |
| POST | `/v1/videos/{id}/cancel` | Cancel an unstarted queued job; JSON body `{}` |
| POST | `/v1/videos/{id}/reconcile` | Revalidate original failed/unknown output only; no model launch; JSON body `{}` |

Use a persistent `request_id` for every intended generation. Identical canonical
input with the same ID returns the original job, including while full or
draining. Changed input with that ID returns 409. Queue full returns 429;
invalid parameters return 400; new IDs while draining return 503. A queued
request has a one-based `queue_position`. Running/completed requests have null.

States are `queued`, `in_progress`, `completed`, `failed`, `unknown` and
`cancelled`. Cancellation after dispatch returns 409 and does not signal a
GPU process. Failed or unknown jobs are not automatically generated again.

## Persistence and shutdown

Queue insertion is committed before visibility to workers. Dispatch to a device
is committed before the unique model child is started. On service restart,
never-dispatched queued work resumes in FIFO order. Interrupted in-progress work
becomes unknown and is not relaunched; original validated output can be
reconciled. Locks are inherited by each owned child so an API crash cannot make
that child's still-busy device look available.

SIGTERM stops new admission and dispatch, preserves queued work and lets active
model children finish. Supervisor manages only this API process. It does not
restart other model services or kill unrelated GPU processes. Supervisor may
restart an unexpectedly exited API; this is not permission to replay an uncertain
model task.

The model is loaded, generated and unloaded for each request. The API is
persistent; weights are not resident between requests. Reported wall/sampling
times are measured per actual task and include the documented loading/offload
work. No warm-latency or 16-task throughput claim is made.

Native 480p buckets are 832×480 / 480×832; 768p buckets are 1344×768 /
768×1344, 24 fps, native AAC 32 kHz stereo. Duration is 5–15 requested seconds,
rounded to the native `17n+5` frame grid. Tested modes are reported separately
from declared supported modes.

Completed output requires pinned source/runner and actual model metadata,
adapter/base hashes, six-step schedule, reference identity, expected geometry,
full audio/video decoding and a SHA256 receipt. This validates the returned
artifact and provenance; subjective dialogue and continuity still need review.

FFmpeg and FFprobe are installed privately in `api16/bin/`, from a release with
a verified SHA256. Only this API's PATH includes them. Startup checks both tools
before accepting a model request. An operator can use `reconcile` after restoring
a validation dependency to inspect the original bytes without paying for or
dispatching another generation.

## Local verification

```bash
python -m pytest --noconftest tests/test_pika_h3_pool.py tests/test_pika_h3_pool_runner.py tests/test_pika_h3_gateway.py -q
ruff check services/pika_h3_pool tests/test_pika_h3_pool.py tests/test_pika_h3_pool_runner.py
```

These checks use CPU fixtures and actual HTTP/SQLite/file-lock/child-process
boundaries without model generation. Live submission, queuing and download
evidence belongs in `outputs/pika-h3-api16-20261105/`.
