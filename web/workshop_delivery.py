"""Host-authorized bridge from a current-IP delivery manifest to real workshop state."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from easel.beeftv_bridge import (
    canvas_state, configured_client, create_canvas, render_collect, render_start, render_status,
)
from easel.video_workshop import WorkshopError, WorkshopStore, _file_lock
from easel.workshop_delivery import load_episodes, produce_episode


def _manifest_request(payload: object, outputs: Path) -> tuple[Path, set[str], bool]:
    if not isinstance(payload, dict) or set(payload) - {"action", "manifestPath", "episodes"}:
        raise ValueError("工坊交付工具参数无效")
    action, relative = payload.get("action"), payload.get("manifestPath")
    if action not in ("plan", "deliver") or not isinstance(relative, str) or not 1 <= len(relative) <= 512:
        raise ValueError("请指定计划或交付操作及当前 IP 下的制作清单")
    if '\\' in relative or any(ord(char) < 32 or ord(char) == 127 for char in relative):
        raise ValueError("清单路径无效")
    path = PurePosixPath(relative)
    if path.is_absolute() or len(path.parts) < 2 or path.suffix != ".json" or any(
            part.startswith((".", "_")) for part in path.parts):
        raise ValueError("制作清单须为当前 IP 内容目录下的 JSON 文件")
    selected = payload.get("episodes", [])
    if not isinstance(selected, list) or len(selected) > 12 or any(
            not isinstance(episode, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", episode)
            for episode in selected) or len(set(selected)) != len(selected):
        raise ValueError("指定集数无效")
    source = outputs.joinpath(*path.parts)
    if source.resolve(strict=True) != source or not source.is_file() or source.stat().st_size > 256 * 1024:
        raise ValueError("当前 IP 的制作清单不可用或过大")
    return source, set(selected), action == "plan"


def run_workshop_delivery(payload: object, *, outputs: Path, ip: str, ideas, authorize) -> list[dict]:
    authorize()
    outputs = outputs.resolve()
    manifest, selected, plan_only = _manifest_request(payload, outputs)
    episodes = load_episodes(manifest, outputs, selected, plan_only)
    if len(episodes) > 12:
        raise ValueError("单次工具最多处理 12 集，请分批处理")
    store = WorkshopStore(outputs, strict_canvas_bindings=True)
    beeftv = None if plan_only else configured_client()

    def collect(project, receipt):
        return render_collect(project, receipt, outputs)

    def api(method, path, body=None):
        authorize()
        if path.startswith("/ideas"):
            return ideas(method, path, body)
        suffix = path.removeprefix("/workshop/projects").strip("/")
        if not suffix:
            return {"projects": store.list()} if method == "GET" else store.create(body, canvas_creator=create_canvas)
        workshop, *parts = suffix.split("/")
        if not parts:
            return store.get(workshop, canvas_state=canvas_state)
        if parts == ["canvas"]:
            return store.attach_canvas(workshop, body["expected_revision"], canvas_creator=create_canvas)
        if parts == ["renders"]:
            return store.start_render(workshop, body["expected_revision"], canvas_state=canvas_state, render_start=render_start)
        if len(parts) == 2 and parts[0] == "renders":
            return store.render_status(workshop, parts[1], render_status=render_status)
        if len(parts) == 3 and parts[0] == "renders" and parts[2] == "collect":
            return store.collect_render(workshop, parts[1], body["expected_revision"], canvas_state=canvas_state,
                                        render_status=render_status, render_collect=collect)
        raise ValueError("无法识别工坊流程步骤")

    with _file_lock(outputs / "_workshop" / "delivery-batch.lock", blocking=False) as acquired:
        if not acquired:
            raise WorkshopError("当前 IP 正在处理另一批成片，请等待原任务并查询回执", 409)
        return [
            produce_episode(api, beeftv, "http://127.0.0.1:7860", ip, episode, 240, plan_only,
                            authorize=authorize)
            for episode in episodes
        ]
