#!/usr/bin/env python3
"""Check source receipts and audio/video integrity before workshop delivery."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
import sys


def digest(path):
    checksum = sha256()
    with path.open("rb") as media:
        for block in iter(lambda: media.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def media_report(path):
    if path.suffix.lower() != ".mp4" or not path.is_file():
        raise ValueError(f"{path}: expected an MP4 file")
    probe = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height:format=duration", "-of", "json", str(path)]
    ))
    video = next((stream for stream in probe["streams"] if stream["codec_type"] == "video"), {})
    width, height = video.get("width", 0), video.get("height", 0)
    if not 0 < width < height:
        raise ValueError(f"{path}: expected a portrait video stream")
    if not any(stream["codec_type"] == "audio" for stream in probe["streams"]):
        raise ValueError(f"{path}: missing audio stream")
    duration = float(probe["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"{path}: expected positive finite duration")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path),
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
        check=True,
    )
    return {
        "path": str(path), "sha256": digest(path), "width": width, "height": height,
        "duration_seconds": duration, "audio_present": True, "full_decode": "pass",
    }


def shot_report(directory, since):
    submission, status, download = [
        json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("submission", "status", "download")
    ]
    job_id = status.get("id")
    if not isinstance(job_id, str) or not job_id.strip() or (
        submission.get("job_id") != job_id or download.get("job_id") != job_id
    ):
        raise ValueError(f"{directory}: submission, status and download job IDs must agree")
    if status.get("status") != "completed":
        raise ValueError(f"{directory}: generation job has not completed")
    created = status.get("created_at")
    if isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created):
        raise ValueError(f"{directory}: missing gateway creation time")
    created_at = datetime.fromtimestamp(created, timezone.utc)
    if since is not None and created_at < since:
        raise ValueError(f"{directory}: generation predates the requested batch")
    report = media_report(directory / "native.mp4")
    if status.get("output", {}).get("sha256") != report["sha256"] or download.get("sha256") != report["sha256"]:
        raise ValueError(f"{directory}: downloaded media does not match both receipt digests")
    return {"job_id": job_id, "created_at_utc": created_at.isoformat(), **report}


def utc_floor(value):
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected an ISO timestamp, e.g. 2026-11-03T17:05:00Z") from error
    if timestamp.utcoffset() != timezone.utc.utcoffset(None):
        raise argparse.ArgumentTypeError("expected an explicit UTC timestamp")
    return timestamp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("master", type=Path, help="assembled portrait MP4")
    parser.add_argument("shots", type=Path, nargs="+", help="directories with native.mp4 and generation receipts")
    parser.add_argument("--since", type=utc_floor, help="reject source jobs created before this UTC timestamp")
    parser.add_argument("--min-duration", type=float, help="minimum master duration in seconds")
    parser.add_argument("--max-duration", type=float, help="maximum master duration in seconds")
    parser.add_argument("--output", type=Path, help="write the successful technical receipt to a JSON file")
    args = parser.parse_args()
    minimum = args.min_duration if args.min_duration is not None else 0
    maximum = args.max_duration if args.max_duration is not None else math.inf
    if not math.isfinite(minimum) or not 0 <= minimum <= maximum or maximum <= 0:
        parser.error("expected 0 <= min-duration <= max-duration and max-duration > 0")

    try:
        master_path = args.master.resolve()
        directories = [directory.resolve() for directory in args.shots]
        if args.output:
            protected = {master_path}
            protected.update(directory / name for directory in directories for name in
                             ("native.mp4", "submission.json", "status.json", "download.json"))
            if args.output.resolve() in protected:
                raise ValueError("--output cannot overwrite source media or receipts")
        shots = [shot_report(directory, args.since) for directory in directories]
        if len({shot["job_id"] for shot in shots}) != len(shots):
            raise ValueError("shot job IDs must be unique")
        master = media_report(master_path)
        if master["width"] * 16 != master["height"] * 9:
            raise ValueError("master must use exact 9:16 framing")
        if not minimum <= master["duration_seconds"] <= maximum:
            raise ValueError(f"master duration {master['duration_seconds']:.3f}s falls outside the requested range")
        receipt = {
            "technical_status": "pass",
            "scope": "Integrity and declared source receipts only. Does not prove which shots appear in the master.",
            "visual_audio_review": "required",
            "workshop_playback_review": "required",
            "source_created_since_utc": args.since.isoformat() if args.since else None,
            "master": master,
            "shots": shots,
        }
        result = json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(result, encoding="utf-8")
        sys.stdout.write(result)
    except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"technical QA failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
