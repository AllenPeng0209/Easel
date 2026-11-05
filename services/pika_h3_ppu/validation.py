"""Strict request and native Pika result validation. Never loads a model."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import warnings

from PIL import Image

MODEL = "pika-h3-flex-6step"
SOURCE_COMMIT = "06bd434ce0b6e287e6f6c4058420460f47ba031b"
ADAPTER_REVISION = "0ddf55ad7067ca1e76812253bd7b4600df87f759"
ADAPTER_SHA = "8ed6115d647ab11c1b38d04e63c4c6e1594128ae312f7b5635cd50563b7b9327"
BASE_REVISION = "42ed227ee7df40d41602854ae760620d6eb651fe"
BUCKET_SHA = "6752bfa788f48959624d82016e2aa08e1a4afb7d00856900f89639e273f1833b"
KNOTS = [1000, 750, 500, 250, 100, 40, 0]
TIMINGS = ("assets_seconds", "conditioning_seconds", "transformer_load_seconds",
           "sampling_seconds", "decode_encode_seconds")
MAX_IMAGE = 10 * 1024 * 1024
MAX_BODY = 15 * 1024 * 1024
GEOMETRY = {("480p", "16:9"): (832, 480), ("480p", "9:16"): (480, 832),
            ("768p", "16:9"): (1344, 768), ("768p", "9:16"): (768, 1344)}


def safe_path(path: Path, *, regular=False):
    """Reject all symlink components, including a symlinked state ancestor."""
    path = Path(os.path.abspath(path))
    for component in (path, *path.parents):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError("Symlinks are forbidden")
    if regular and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Expected a regular file")
    return path


def read_regular(path, limit):
    safe_path(path, regular=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Expected a regular file")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("File size limit exceeded")
        return data


def sha256_file(path):
    safe_path(path, regular=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest = hashlib.sha256()
    with os.fdopen(fd, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def request_fingerprint(request):
    canonical = {key: value for key, value in request.items() if key != "request_id"}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def normalize(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    allowed = {"model", "prompt", "seconds", "duration", "size", "resolution", "seed",
               "request_id", "images", "videos", "audios"}
    if set(payload) - allowed:
        raise ValueError("Unsupported request fields")
    if payload.get("model") != MODEL:
        raise ValueError("Only pika-h3-flex-6step is supported")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000 or "\x00" in prompt:
        raise ValueError("prompt must contain 1 to 8000 characters without NUL")
    if "seconds" in payload and "duration" in payload:
        raise ValueError("Use seconds or duration, never both")
    seconds = payload.get("seconds", payload.get("duration", 5))
    if type(seconds) is not int or not 5 <= seconds <= 15:
        raise ValueError("seconds must be an integer between 5 and 15")
    seed = payload.get("seed", 0)
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("seed must be an integer in [0, 2**63)")
    aspect, resolution = payload.get("size", "16:9"), payload.get("resolution", "480p")
    if not isinstance(aspect, str) or not isinstance(resolution, str) or (resolution, aspect) not in GEOMETRY:
        raise ValueError("Supported size 16:9/9:16; resolution 480p/768p")
    request_id = payload.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id)):
        raise ValueError("Invalid request_id")
    for name in ("videos", "audios"):
        if name in payload and payload[name] != []:
            raise ValueError("Audio and video references are unsupported")
    images = payload.get("images", [])
    if not isinstance(images, list) or len(images) > 1:
        raise ValueError("images must contain at most one inline image")
    raw, mime = None, None
    if images:
        item = images[0]
        if not isinstance(item, dict) or set(item) != {"data_url"} or not isinstance(item["data_url"], str):
            raise ValueError("An image must contain only data_url")
        match = re.fullmatch(r"data:(image/(?:png|jpeg));base64,([A-Za-z0-9+/=]+)", item["data_url"])
        if not match or len(item["data_url"]) > (MAX_IMAGE + 2) // 3 * 4 + 40:
            raise ValueError("Image must be an inline PNG/JPEG up to 10 MiB")
        mime = match[1]
        try:
            raw = base64.b64decode(match[2], validate=True)
            if not raw or len(raw) > MAX_IMAGE:
                raise ValueError("Image size is invalid")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format != {"image/png": "PNG", "image/jpeg": "JPEG"}[mime]:
                        raise ValueError("Image MIME does not match decoded bytes")
                    if min(image.size) < 16 or max(image.size) > 8192 or image.width * image.height > 32_000_000:
                        raise ValueError("Image dimensions out of range")
                    if getattr(image, "n_frames", 1) != 1:
                        raise ValueError("Animated images are unsupported")
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
        except (OSError, binascii.Error, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValueError("Invalid inline image") from error
    width, height = GEOMETRY[(resolution, aspect)]
    frames = seconds * 24 + (5 - seconds * 24) % 17
    return {"model": MODEL, "prompt": prompt, "seconds": seconds, "size": aspect,
            "resolution": resolution, "seed": seed, "request_id": request_id,
            "task": "i2va" if images else "t2va", "width": width, "height": height,
            "frames": frames, "image_mime": mime,
            "image_sha256": hashlib.sha256(raw).hexdigest() if raw else None}, raw


def validate_result(output: Path, request):
    """Authenticate native model metadata, then independently decode the whole MP4."""
    output = safe_path(output, regular=True)
    metadata = json.loads(read_regular(output.with_suffix(".json"), 256 * 1024))
    expected = {"task": request["task"], "prompt": request["prompt"], "seed": request["seed"],
                "requested_seconds": request["seconds"], "resolution": request["resolution"],
                "aspect": request["size"], "adapter_sha256": ADAPTER_SHA,
                "adapter_revision": ADAPTER_REVISION, "bucket_sha256": BUCKET_SHA,
                "noise_version": "pika-flex-noise-v1", "numerics_policy": "bf16_conditioning_v1",
                "base": {"repo_id": "MiniMaxAI/MiniMax-H3", "revision": BASE_REVISION},
                "reference_order": [], "input_sha256": {"first_image": request["image_sha256"],
                                                         "last_image": None, "references": []}}
    for name, value in expected.items():
        if metadata.get(name) != value or (type(value) is int and type(metadata.get(name)) is not int):
            raise ValueError("Native Pika result identity mismatch: " + name)
    sampling = metadata.get("sampling", {})
    if (sampling != {"knots": KNOTS, "video_shift": 12.0, "audio_shift": 3.0, "method": "renoise"}
            or any(type(value) is not int for value in sampling.get("knots", []))):
        raise ValueError("Native Pika six-step schedule mismatch")
    frames, width, height = request["frames"], request["width"], request["height"]
    geometry = {"num_frames": frames, "width": width, "height": height,
                "num_latent_frames": (frames - 5) // 17 * 5 + 2,
                "latent_height": height // 16, "latent_width": width // 16,
                "num_audio_latents": round(frames / 24 * 40)}
    if metadata.get("geometry") != geometry:
        raise ValueError("Native geometry mismatch")
    timings = metadata.get("timings", {})
    for stage in TIMINGS:
        value = timings.get(stage)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Invalid native timing")
    dependencies = metadata.get("dependencies")
    if (not isinstance(metadata.get("torch"), str) or not metadata["torch"].startswith("2.9.") or
            metadata.get("gpu") != "PPU-ZW810E" or
            type(metadata.get("sampling_peak_allocated_bytes")) is not int or
            metadata["sampling_peak_allocated_bytes"] <= 0 or not isinstance(dependencies, dict) or
            set(dependencies) != {"diffusers", "transformers", "peft", "av", "safetensors"} or
            any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,100}", value)
                for value in dependencies.values())):
        raise ValueError("Invalid PPU runtime audit")
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams",
                            "-show_format", "-of", "json", str(output)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    info = json.loads(probe.stdout)
    video = [x for x in info["streams"] if x["codec_type"] == "video"]
    audio = [x for x in info["streams"] if x["codec_type"] == "audio"]
    if len(video) != 1 or len(audio) != 1 or len(info["streams"]) != 2:
        raise ValueError("Expected exactly one video and one native audio stream")
    v, a = video[0], audio[0]
    if ((v["width"], v["height"]) != (width, height) or v["avg_frame_rate"] != "24/1"
            or int(v.get("nb_read_frames", 0)) != frames or v["codec_name"] != "h264"
            or a["codec_name"] != "aac" or int(a["sample_rate"]) != 32000 or a["channels"] != 2
            or abs(float(info["format"]["duration"]) - frames / 24) > .15
            or abs(float(a.get("duration", 0)) - frames / 24) > .15):
        raise ValueError("Native audio/video media geometry mismatch")
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-map", "0:v:0",
                    "-map", "0:a:0", "-f", "null", "-"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
    return {"model": MODEL, "adapter_revision": ADAPTER_REVISION, "adapter_sha256": ADAPTER_SHA,
            "nfe": 6, "sampling_knots": KNOTS, "native_audio": True, "resident": False,
            "width": width, "height": height, "frames": frames, "fps": 24,
            "duration": frames / 24, "requested_seconds": request["seconds"],
            "timings": {name: timings[name] for name in TIMINGS},
            "runtime": {"torch": metadata["torch"], "gpu": metadata["gpu"],
                        "dependencies": dependencies,
                        "sampling_peak_allocated_bytes": metadata["sampling_peak_allocated_bytes"]},
            "timing_note": "sampling includes transfers/synchronization; pure DiT was not measured",
            "sha256": sha256_file(output)}
