#!/usr/bin/env python3
"""Submit or resume one Ref2VA shot against a local H3 gateway."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MEDIA_TYPES = {
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".mp4": ("video", "video/mp4"),
    ".wav": ("audio", "audio/wav"),
}
MODEL = "minimax-h3-ref2va-ref11-8step"
PICTURE = "Picture delivery: Unmarked live-action camera rushes. Every visible pixel belongs to the physical scene, actors, wardrobe, or props."
DIALOGUE = "All Mandarin dialogue is heard as synchronized speech. Keep lines and editorial graphics out of the picture."


class ShotError(Exception):
    """A shot cannot proceed as requested."""


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def gateway_url(raw_url):
    parts = urlsplit(raw_url)
    if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parts.username is not None or parts.password is not None
            or parts.path not in {"", "/"} or parts.query or parts.fragment):
        raise ShotError("H3 base URL must be an HTTP loopback origin")
    return raw_url.rstrip("/")


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def encoded(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")


class Gateway:
    def __init__(self, base_url):
        credential_file = os.environ.get("EASEL_H3_TOKEN_FILE")
        if not credential_file:
            raise ShotError("Set EASEL_H3_TOKEN_FILE to the operator-provided H3 credential file")
        try:
            token = Path(credential_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise ShotError("Cannot read the H3 credential file") from None
        if not token or not token.isascii() or any(char.isspace() for char in token):
            raise ShotError("The H3 credential file must contain one bearer token")
        self.base_url = base_url
        self.headers = {"Authorization": "Bearer " + token}
        self.opener = build_opener(ProxyHandler({}), RejectRedirects())

    def open(self, route, data=None, content_type=None):
        headers = dict(self.headers)
        if content_type:
            headers["Content-Type"] = content_type
        return self.opener.open(Request(self.base_url + route, data=data, headers=headers), timeout=90)

    def json(self, route, data=None, content_type=None):
        with self.open(route, data, content_type) as response:
            return json.load(response)


def recipe_for(args, references, base_url):
    prompt = (args.shot_dir / "prompt.txt").read_text(encoding="utf-8").strip()
    if not prompt:
        raise ShotError("prompt.txt must contain the shot prompt")
    prompt += "\n\n" + PICTURE
    if not args.silent:
        prompt += " " + DIALOGUE
    settings = {"model": MODEL, "prompt": prompt, "seconds": args.seconds,
                "size": args.size, "seed": args.seed}
    media = []
    for path in references:
        if path.suffix.lower() not in MEDIA_TYPES:
            raise ShotError("References must be JPEG, PNG, MP4, or WAV files")
        kind, mime = MEDIA_TYPES[path.suffix.lower()]
        media.append({"kind": kind, "mime": mime, "sha256": sha256(path)})
    return settings, media, hashlib.sha256(encoded([base_url, settings, media])).hexdigest()


def prepare_receipt(gateway, args, references, settings, media, fingerprint):
    payload = {**settings, "images": [], "videos": [], "audios": []}
    ref_receipts = []
    for path, metadata in zip(references, media, strict=True):
        kind = metadata["kind"]
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
            raise ShotError("A reference changed while preparing the request")
        upload = gateway.json("/v1/files?purpose=ref_" + kind, raw, metadata["mime"])
        payload[kind + "s"].append(upload["id"])
        ref_receipts.append({**metadata, "file": str(path), "file_id": upload["id"]})
    # A new shot directory represents a new generation, even for identical settings.
    identity = hashlib.sha256(encoded([str(args.shot_dir.resolve()), fingerprint])).hexdigest()
    payload["request_id"] = "easel-h3-" + identity[:32]
    return {"payload": payload, "references": ref_receipts, "input_sha256": fingerprint,
            "request_sha256": hashlib.sha256(encoded(payload)).hexdigest(),
            "submission_state": "submitting"}


def run(args):
    directory = args.shot_dir
    receipt_path = directory / "submission.json"
    status_path = directory / "status.json"
    download_path = directory / "download.json"
    video_path = directory / "native.mp4"
    partial = directory / "native.mp4.partial"
    base_url = gateway_url(os.environ.get("EASEL_H3_BASE_URL", "http://127.0.0.1:8881"))
    references = [args.reference, *args.extra_references]
    settings, media, fingerprint = recipe_for(args, references, base_url)

    receipt = read_json(receipt_path) if receipt_path.exists() else None
    if receipt and (receipt.get("input_sha256") != fingerprint
                    or hashlib.sha256(encoded(receipt["payload"])).hexdigest() != receipt.get("request_sha256")):
        raise ShotError("Saved request differs from these inputs. Resume with the original inputs or use a new shot directory")
    if receipt and video_path.is_file() and download_path.is_file():
        download = read_json(download_path)
        if download.get("job_id") == receipt.get("job_id") and sha256(video_path) == download.get("sha256"):
            print("verified download", download["sha256"], flush=True)
            return 0

    gateway = Gateway(base_url)
    if receipt is None:
        receipt = prepare_receipt(gateway, args, references, settings, media, fingerprint)
        save_json(receipt_path, receipt)
    if "job_id" not in receipt:
        # An interrupted POST is replayed with the exact saved idempotency key.
        job = gateway.json("/v1/videos", encoded(receipt["payload"]), "application/json")
        receipt.update(job_id=job["id"], submission_state="accepted")
        save_json(receipt_path, receipt)
        save_json(status_path, job)
    job_id = receipt["job_id"]
    print("job", job_id, flush=True)
    if args.submit_only:
        return 0

    route = "/v1/videos/" + quote(job_id, safe="")
    deadline = time.monotonic() + args.wait_seconds
    previous = None
    while True:
        job = gateway.json(route)
        save_json(status_path, job)
        status = job["status"]
        if status != previous:
            print("status", status, flush=True)
            previous = status
        if status == "completed":
            break
        if status in {"failed", "cancelled"}:
            return 2
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print("still running; resume in the same shot directory", file=sys.stderr)
            return 4
        time.sleep(min(12, remaining))

    with gateway.open(route + "/content") as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, 1024 * 1024)
    job = gateway.json(route)
    save_json(status_path, job)
    digest = sha256(partial)
    if digest != job.get("output", {}).get("sha256"):
        raise ShotError("Downloaded bytes do not match the gateway output digest")
    partial.replace(video_path)
    save_json(download_path, {"sha256": digest, "job_id": job_id})
    print("verified download", digest, flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shot_dir", type=Path, help="existing directory containing prompt.txt")
    parser.add_argument("reference", type=Path)
    parser.add_argument("seconds", type=int, choices=range(1, 15))
    parser.add_argument("extra_references", nargs="*", type=Path)
    parser.add_argument("--size", choices=("9:16", "16:9"), default="9:16")
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--submit-only", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=1800)
    args = parser.parse_args()
    if args.wait_seconds < 0:
        parser.error("--wait-seconds must be non-negative")
    try:
        with (args.shot_dir / ".h3-shot.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ShotError("Another process is handling this shot directory") from None
            return run(args)
    except ShotError as error:
        print(str(error), file=sys.stderr)
    except HTTPError as error:
        print("H3 gateway HTTP", error.code, file=sys.stderr)
    except (URLError, TimeoutError):
        print("H3 gateway connection failed; resume in the same shot directory", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError):
        print("Invalid shot files or gateway response; check saved receipts and retry the same directory", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
