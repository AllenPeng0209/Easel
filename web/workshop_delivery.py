"""Host-authorized bridge from a current-IP delivery manifest to real workshop state."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import json
import re

from easel.video_workshop import WorkshopError, WorkshopStore, _file_lock
from easel.workshop_delivery import load_episodes, produce_episode


def _manifest_request(payload: object, outputs: Path) -> tuple[Path, set[str], bool]:
    if not isinstance(payload, dict) or set(payload) - {"action", "manifestPath", "episodes"}:
        raise ValueError("工坊交付工具参数无效")
    action, relative = payload.get("action"), payload.get("manifestPath")
    if action not in ("plan", "deliver", "update") or not isinstance(relative, str) or not 1 <= len(relative) <= 512:
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


def run_workshop_delivery(payload: object, *, outputs: Path, ip: str, authorize) -> list[dict]:
    authorize()
    outputs = outputs.resolve()
    manifest, selected, plan_only = _manifest_request(payload, outputs)
    if payload['action'] == 'update':
        from web.workshop_routes import Production
        raw = json.loads(manifest.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or set(raw) != {'workshopId', 'expectedRevision', 'production'}:
            raise ValueError('更新清单须指定当前工坊、内容版本和制作内容')
        request = Production.model_validate({'expected_revision': raw['expectedRevision'], **raw['production']})
        authorize()
        project = WorkshopStore(outputs).production(raw['workshopId'], request.expected_revision,
            request.model_dump(exclude={'expected_revision'}, exclude_none=True))
        return [{'workshopId': project['id'], 'revision': project['revision'], 'stage': project['stage']}]
    episodes = load_episodes(manifest, outputs, selected, plan_only)
    if len(episodes) > 12:
        raise ValueError("单次工具最多处理 12 集，请分批处理")
    store = WorkshopStore(outputs)
    def api(method, path, body=None):
        authorize()
        suffix = path.removeprefix("/workshop/projects").strip("/")
        if not suffix:
            return {"projects": store.list()} if method == "GET" else store.create(body)
        workshop, *parts = suffix.split("/")
        if not parts:
            return store.get(workshop)
        if parts == ['production']:
            from web.workshop_routes import Production
            request = Production.model_validate(body)
            return store.production(workshop, request.expected_revision, request.model_dump(exclude={'expected_revision'}, exclude_none=True))
        if parts == ['deliverables']:
            return store.deliver(workshop, body['expected_revision'], body['path'])
        raise ValueError("无法识别工坊流程步骤")

    with _file_lock(outputs / "_workshop" / "delivery-batch.lock", blocking=False) as acquired:
        if not acquired:
            raise WorkshopError("当前 IP 正在处理另一批成片，请等待原任务并查询回执", 409)
        return [
            produce_episode(api, "http://127.0.0.1:7860", ip, episode, 240, plan_only,
                            authorize=authorize)
            for episode in episodes
        ]


async def prepare_workshop_publications(rows, *, outputs, workflow, authorize):
    """AI may prepare the exact subject; only a human workshop review executes it."""
    from web.workshop_publication import WorkshopPublication
    from web.workshop_routes import PublishPreparation
    bridge = WorkshopPublication(WorkshopStore(outputs), workflow)
    result = []
    for raw in rows:
        row = dict(raw)
        publication = row.pop('publication', None)
        if publication is not None and row.get('artifactId'):
            authorize()
            request = PublishPreparation.model_validate({**publication,
                'expected_revision': row['revision'], 'artifact_id': row['artifactId']})
            prepared = await bridge.prepare(row['workshopId'], request.model_dump())
            row['publicationReview'] = {'state': 'pending', 'subject': prepared['approval']['subject']}
        result.append(row)
    return result
