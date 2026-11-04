"""Shared strategy-to-workshop workflow, using an authenticated host transport."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import quote

from integrations.beeftv.media_import import import_media
from scripts.video_workshop import identifier, relative_media


def probe_video(source: Path) -> tuple[int, int, int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,avg_frame_rate", "-of", "json", str(source)],
        capture_output=True, text=True, check=True, timeout=20,
    )
    info = json.loads(result.stdout)
    video = next(item for item in info["streams"] if item["codec_type"] == "video")
    if not any(item["codec_type"] == "audio" for item in info["streams"]):
        raise ValueError(f"{source.name} 缺少成片音轨")
    numerator, denominator = map(int, video["avg_frame_rate"].split("/"))
    return (int(float(info["format"]["duration"]) * 1000), video["width"], video["height"],
            max(1, round(numerator / denominator)))


def load_episodes(manifest: Path, outputs: Path, selected: set[str], plan_only: bool) -> list[dict]:
    relative_manifest = manifest.resolve(strict=True).relative_to(outputs).as_posix()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    series = data["series"].strip()
    batch = hashlib.sha256(f"{relative_manifest}\n{series}".encode()).hexdigest()[:16]
    legacy_batch = hashlib.sha256(f"{manifest.stem}\n{series}".encode()).hexdigest()[:16]
    episodes = []
    ids = set()
    for item in data["episodes"]:
        episode_id = identifier(item["id"])
        if episode_id in ids:
            raise ValueError(f"集数重复：{episode_id}")
        ids.add(episode_id)
        if selected and episode_id not in selected:
            continue
        title = f"{series}｜{item['title'].strip()}"
        if not series or not item["title"].strip() or len(title) > 120:
            raise ValueError("工坊标题须含系列名及集名且不超过 120 个字")
        path = relative_media(item["path"])
        sources = [relative_media(value) for value in item.get("timeline", [path])]
        if not sources or len(sources) > 30:
            raise ValueError("时间线须提供 1–30 段已剪辑的视频")
        brief = item["brief"].strip()
        if not brief or len(brief) > 49000:
            raise ValueError("每集必须提供完整制作简报")
        aspect = item.get("aspectRatio", "9:16")
        if aspect not in {"9:16", "16:9", "1:1", "4:5"}:
            raise ValueError("工坊画幅必须是 9:16、16:9、1:1 或 4:5")
        episode = {"id": episode_id, "title": title, "brief": brief,
                   "marker": f"Easel 制作批次 {batch}/{episode_id}", "aspect": aspect}
        linked = item.get("workshopId")
        if linked is not None:
            if not isinstance(linked, str) or not re.fullmatch(r"[a-f0-9]{32}", linked):
                raise ValueError("关联工坊编号无效")
            episode["linked_workshop"] = linked
            episode["legacy_marker"] = f"Easel 制作批次 {legacy_batch}/{episode_id}"
        if not plan_only:
            media = {}
            for relative in dict.fromkeys([path, *sources]):
                source = outputs / relative
                if source.resolve(strict=True) != source or not source.is_file():
                    raise ValueError(f"成片路径须在当前 IP 的 outputs 内：{relative}")
                media[relative] = (source, probe_video(source))
            _, width, height, fps = media[path][1]
            episode["media"] = [media[relative] for relative in sources]
            episode["output"] = {"width": width, "height": height, "fps": fps, "sampleRate": 48000}
        episodes.append(episode)
    if not episodes:
        raise ValueError("请提供至少一集已完成的视频")
    if not selected.issubset(ids):
        raise ValueError("指定集数不在制作清单中")
    return episodes


def save_timeline(beeftv, canvas: str, workshop_id: str, episode: dict, authorize) -> None:
    authorize()
    snapshot = beeftv.operation("canvas.get", {"canvasId": canvas})["canvas"]
    existing = snapshot.get("timeline") or {}
    if existing.get("clips"):
        if all(clip.get("id", "").startswith(f"{workshop_id}-") for clip in existing["clips"]):
            return
        raise ValueError("当前工坊已经有人工时间线，请使用新制作批次")
    clips = []
    cursor = 0
    revision = snapshot["revision"]
    for index, (source, info) in enumerate(episode["media"], start=1):
        authorize()
        title = f"{index:02d} · {source.stem}"
        asset = import_media(source, title, canvas)
        revision = asset["revision"]
        duration = info[0]
        clips.append({"id": f"{workshop_id}-{index}", "kind": "video", "nodeId": asset["nodeId"],
                      "trackId": "picture", "title": title, "startMs": cursor, "durationMs": duration})
        cursor += duration
    authorize()
    beeftv.operation("canvas.timeline.update", {
        "canvasId": canvas, "expectedRevision": revision, "timeline": {
            "version": 2, "durationMs": cursor,
            "tracks": [{"id": "picture", "kind": "video", "label": "成片镜头"}],
            "clips": clips, "output": episode["output"],
        },
    }, operation_id=f"easel-episode-timeline-{workshop_id}")


def produce_episode(api, beeftv, base: str, ip: str, episode: dict, timeout: float,
                    plan_only: bool = False, *, authorize=lambda: None) -> dict:
    title, marker = episode["title"], episode["marker"]
    projects = api("GET", "/workshop/projects")["projects"]
    linked = episode.get("linked_workshop")
    if linked is not None:
        project = next((row for row in projects if row["id"] == linked), None)
        lines = project.get("brief", "").splitlines() if project else ()
        if not project or project.get("archived_at") or (marker not in lines and (
                project["title"] != title or episode["legacy_marker"] not in lines)):
            raise ValueError("清单中的关联工坊不属于当前制作批次")
    else:
        project = next((row for row in projects if marker in row.get("brief", "").splitlines()), None)
    if project is None:
        project = api("POST", "/workshop/projects", {
            "title": title, "brief": f"{episode['brief']}\n\n{marker}",
            "aspect_ratio": episode["aspect"],
        })
    workshop = project["id"]
    endpoint = f"/workshop/projects/{workshop}"
    link = f"{base}/#/business/{quote(ip, safe='')}/workshop/{workshop}"
    ideas = api("GET", "/ideas")
    idea = next((row for row in ideas if re.fullmatch(
        re.escape(link) + r"(?:/artifact/[a-f0-9]{32})?", (row.get("provenance") or {}).get("url", ""))), None)
    if idea is None:
        idea = api("POST", "/ideas", {
            "title": title, "note": episode["brief"], "source": "成片制作",
            "status": "doing", "provenance": {"platform": "easel_workshop", "url": link},
        })
    identifiers = {"episode": episode["id"], "title": title, "ideaId": idea["id"], "workshopId": workshop,
                   "ideaUrl": f"{base}/#/business/{quote(ip, safe='')}/idea/{idea['id']}"}
    if plan_only:
        return {**identifiers, "workshopUrl": link, "stage": project["stage"]}
    project = api("GET", endpoint)
    if not project["beeftv_canvas_id"]:
        project = api("POST", endpoint + "/canvas", {"expected_revision": project["revision"]})
    if not project["beeftv_canvas_id"]:
        raise RuntimeError("无法关联 BeefTV 画布，请恢复运行时后重试原批次")
    if not project["deliverables"]:
        save_timeline(beeftv, project["beeftv_canvas_id"], workshop, episode, authorize)
        revision = {"expected_revision": project["revision"]}
        render = api("POST", endpoint + "/renders", revision)
        render_path = endpoint + "/renders/" + render["id"]
        deadline = time.monotonic() + timeout
        while render["status"] in {"queued", "running"}:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"原渲染 {render['id']} 仍在运行，可再次执行原批次收取")
            time.sleep(2)
            render = api("GET", render_path)
        if render["status"] != "succeeded":
            raise RuntimeError(f"渲染 {render['id']} 状态为 {render['status']}，请在工坊核对")
        project = api("POST", render_path + "/collect", revision)
    artifact = project["deliverables"][-1]
    final_link = f"{link}/artifact/{artifact['id']}"
    media_path = "/api/projects/" + quote(ip, safe="") + "/media/" + quote(artifact["path"])
    if idea["status"] == "doing":
        idea = api("PUT", "/ideas/" + identifier(idea["id"]),
                   {key: idea[key] for key in ("title", "note", "source")} | {
                       "status": "done", "provenance": {"platform": "easel_workshop", "url": final_link}})
    return {**identifiers, "artifactId": artifact["id"], "canvasId": project["beeftv_canvas_id"],
            "renderId": artifact["render_id"], "engineRevision": artifact["engine_revision"],
            "sha256": artifact["sha256"], "path": artifact["path"],
            "workshopUrl": final_link, "mediaUrl": base + media_path}
