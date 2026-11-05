"""Deliver production manifests directly to the authenticated video workshop."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

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
        if 'production' in item:
            episode['production'] = item['production']
        if 'publication' in item:
            episode['publication'] = item['publication']
        episode['path'] = path
        if not plan_only:
            media = {}
            for relative in dict.fromkeys([path, *sources]):
                source = outputs / relative
                if source.resolve(strict=True) != source or not source.is_file():
                    raise ValueError(f"成片路径须在当前 IP 的 outputs 内：{relative}")
                media[relative] = (source, probe_video(source))
            episode['master'] = media[path][0]
            _, width, height, fps = media[path][1]
            episode["media"] = [media[relative] for relative in sources]
            episode["output"] = {"width": width, "height": height, "fps": fps, "sampleRate": 48000}
        episodes.append(episode)
    if not episodes:
        raise ValueError("请提供至少一集已完成的视频")
    if not selected.issubset(ids):
        raise ValueError("指定集数不在制作清单中")
    return episodes


def produce_episode(api, base: str, ip: str, episode: dict, timeout: float,
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
    identifiers = {"episode": episode["id"], "title": title, "workshopId": workshop}
    if plan_only:
        return {**identifiers, "workshopUrl": link, "stage": project["stage"]}
    project = api("GET", endpoint)
    if episode.get('production'):
        project = api('PUT', endpoint + '/production', {'expected_revision': project['revision'], **episode['production']})
    current = project['deliverables'][-1] if project['deliverables'] else None
    digest = _digest_master(episode['master'])
    if not current or current['sha256'] != digest or current['revision'] != project['revision']:
        project = api('POST', endpoint + '/deliverables', {'expected_revision': project['revision'], 'path': episode['path']})
    artifact = project["deliverables"][-1]
    final_link = f"{link}/artifact/{artifact['id']}"
    media_path = "/api/projects/" + quote(ip, safe="") + "/media/" + quote(artifact["path"])
    return {**identifiers, "artifactId": artifact["id"], "revision": project["revision"],
            "sha256": artifact["sha256"], "path": artifact["path"],
            "workshopUrl": final_link, "mediaUrl": base + media_path,
            **({"publication": episode["publication"]} if episode.get("publication") else {})}


def _digest_master(path):
    from easel.video_workshop import _digest
    return _digest(path)
