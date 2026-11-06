"""Typed Host-owned video admission under a human-frozen production plan.

The native agent never receives connection credentials or network authority.
An uncertain paid submission is observed by its original node/task identity;
it is never automatically replayed, even with the same operation key.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time
from urllib.parse import quote

from easel.video_workshop import WorkshopError, WorkshopStore, _digest, _file_lock


_ID = r"[a-f0-9]{32}"
_CLIP = r"[A-Za-z0-9_-]{1,80}"
_TOKEN = r"[A-Za-z0-9:_-]{1,160}"
_PLAN_ACTIONS = {"plan", "approve_plan", "plan_status"}
_ACTIONS = {"catalog", "prepare", "propose", "submit", "status", "collect", *_PLAN_ACTIONS}
_SPEC = {"modelId", "aspectRatio", "seconds", "prompt", "resolution"}
_REF11_MODEL = "easel-ppu-h3-ref11-local::minimax-h3-ref2va-ref11-8step"
_PIKA_MODEL = "easel-ppu-pika-h3-local::pika-h3-flex-6step"
_H3_QUERY_MODELS = {"easel-ppu-h3-local::minimax-h3-dense8", "easel-ppu-h3-local::minimax-h3-base", _REF11_MODEL, _PIKA_MODEL}
_H3_JOB = r"video_[a-f0-9]{32}"
_PROVIDER_QUERY_INTERVAL = 30
_PROVIDER_PROGRESS = {"queued", "in_progress", "running", "processing"}


def video_generation_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("action") not in _ACTIONS:
        raise ValueError("视频工具操作无效")
    action = payload["action"]
    if action in _PLAN_ACTIONS:
        fields = {"action", "workshopId", "manifestPath" if action == "plan" else "planId"}
        if set(payload) != fields or not isinstance(payload.get("workshopId"), str) or not re.fullmatch(_ID, payload["workshopId"]):
            raise ValueError("请使用当前 IP 工坊中的制作计划")
        if action == "plan":
            relative = payload["manifestPath"]
            if (not isinstance(relative, str) or not 1 <= len(relative) <= 512 or '\\' in relative
                    or ':' in relative or any(ord(char) < 32 for char in relative)):
                raise ValueError("制作清单路径无效")
            path = PurePosixPath(relative)
            if (path.is_absolute() or len(path.parts) < 2 or path.suffix != '.json'
                    or str(path) != relative or any(part.startswith(('.', '_')) for part in path.parts)):
                raise ValueError("制作清单须为当前 IP 内容目录内的 JSON 文件")
        elif not isinstance(payload.get("planId"), str) or not re.fullmatch(_ID, payload["planId"]):
            raise ValueError("制作计划编号无效")
        return dict(payload)
    allowed = {"action"} if action == "catalog" else {"action", "workshopId", "clipId"}
    if action == "prepare":
        allowed.add("shotId")
        if "shotId" in payload and (not isinstance(payload["shotId"], str) or not re.fullmatch(_CLIP, payload["shotId"])):
            raise ValueError("原分镜编号无效")
        allowed |= {"planId"} if "planId" in payload else _SPEC
    if set(payload) - allowed:
        raise ValueError("视频工具不接受身份、连接信息、文件路径或自定任务编号")
    if action != "catalog" and (
            not isinstance(payload.get("workshopId"), str) or not re.fullmatch(_ID, payload["workshopId"])
            or not isinstance(payload.get("clipId"), str) or not re.fullmatch(_CLIP, payload["clipId"])):
        raise ValueError("请使用当前 IP 的工坊项目及固定片段编号")
    if action == "prepare" and "planId" in payload:
        if not isinstance(payload["planId"], str) or not re.fullmatch(_ID, payload["planId"]):
            raise ValueError("制作计划编号无效")
    if action == "prepare" and "planId" not in payload:
        for key, maximum in (("modelId", 200), ("aspectRatio", 12), ("prompt", 10000), ("resolution", 24)):
            value = payload.get(key, "720p" if key == "resolution" else None)
            if (not isinstance(value, str) or not value.strip() or len(value) > maximum
                    or any(ord(char) < 32 and char not in "\n\t" or 127 <= ord(char) < 160 for char in value)):
                raise ValueError("请明确模型、画幅、时长及制作提示词")
        if type(payload.get("seconds")) is not int or not 1 <= payload["seconds"] <= 60:
            raise ValueError("视频时长须为明确的整数秒")
    return dict(payload)


def probe_generated_video(path: Path, spec: dict, *, source: bool = False) -> dict:
    """Require genuine decodable audiovisual media, never a renamed still image."""
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
            "format=format_name,duration:stream=codec_type,width,height,avg_frame_rate,nb_read_frames",
            "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=45)
        data = json.loads(result.stdout)
        video = next(item for item in data["streams"] if item["codec_type"] == "video")
        duration = float(data["format"]["duration"])
        numerator, denominator = map(int, video["avg_frame_rate"].split("/"))
        frames = int(video["nb_read_frames"])
        width, height = video["width"], video["height"]
        ratio_w, ratio_h = map(int, spec["aspectRatio"].split(":"))
        audio = any(item["codec_type"] == "audio" for item in data["streams"])
        # Pika's native buckets intentionally differ from the display ratio.
        # Admit only its exact verified source geometry; derived delivery and
        # every other model retain the existing general aspect-ratio check.
        if source and spec.get("modelId") == _PIKA_MODEL and spec.get("resolution") == "480p":
            aspect_valid = (width, height) == {"16:9": (832, 480), "9:16": (480, 832)}.get(spec["aspectRatio"])
        else:
            aspect_valid = abs(width / height - ratio_w / ratio_h) <= .035
        if (not math.isfinite(duration) or abs(duration - spec["seconds"]) > max(1, spec["seconds"] * .15)
                or frames < 2 or width <= 0 or height <= 0 or denominator <= 0 or numerator <= 0 or not audio
                or not aspect_valid
                or not set(data["format"]["format_name"].split(",")) & {"mov", "mp4", "matroska", "webm"}):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError, StopIteration, subprocess.SubprocessError):
        raise WorkshopError("生成资源未通过真实视频、音轨、时长与画幅校验，尚未登记成片", 422) from None
    return {"durationMs": round(duration * 1000), "width": width, "height": height,
            "fps": numerator / denominator, "frames": frames, "hasAudio": audio}


def target_dimensions(spec: dict) -> tuple[int, int]:
    resolution = spec["resolution"]
    if not re.fullmatch(r"(?:480|720|1080|1440|2160)p", resolution):
        raise WorkshopError("当前 Host 只支持明确的 480p 至 2160p 交付规格", 422)
    short = int(resolution[:-1])
    width, height = map(int, spec["aspectRatio"].split(":"))
    if not 1 <= width <= 32 or not 1 <= height <= 32:
        raise WorkshopError("视频交付画幅无效", 422)
    if width >= height:
        return round(short * width / height / 2) * 2, short
    return short, round(short * height / width / 2) * 2


def normalize_generated_video(source: Path, destination: Path, spec: dict) -> dict:
    """Fixed local AV transform; never asks a provider for another generation."""
    width, height = target_dimensions(spec)
    descriptor, name = tempfile.mkstemp(dir=destination.parent, prefix=".normalized-", suffix=".mp4")
    os.close(descriptor)
    partial = Path(name)
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0", "-map_metadata", "-1",
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-threads", "2", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-y", str(partial)],
            capture_output=True, text=True, check=True, timeout=180)
        media = probe_generated_video(partial, spec)
        if (media["width"], media["height"]) != (width, height):
            raise ValueError
        os.replace(partial, destination)
        return media
    except (OSError, ValueError, subprocess.SubprocessError):
        raise WorkshopError("原始生成视频已保留，本机规格转换尚未完成；请收取原任务，不会再次生成", 422) from None
    finally:
        partial.unlink(missing_ok=True)


class VideoGenerationService:
    def __init__(self, *, outputs: Path, state_directory: Path, client, actor_id: str, ip: str,
                 session_id: str, authorize, repository: Path | None = None):
        if not all(isinstance(value, str) and value and len(value) <= 512
                   for value in (actor_id, ip, session_id)):
            raise ValueError("视频操作缺少真实 Host 用户、IP 或会话")
        self.outputs = Path(outputs).resolve()
        self.client = client
        self.actor_id, self.ip, self.session_id = actor_id, ip, session_id
        self.authorize = authorize
        self.store = WorkshopStore(self.outputs)
        state_directory = Path(state_directory)
        if state_directory.is_symlink() or state_directory.resolve().is_relative_to(self.outputs.parent):
            raise ValueError("视频付费回执与会话预算必须保存在 Agent 可写工作区之外")
        self.directory = state_directory.resolve()
        self.repository = Path(repository).resolve() if repository is not None else None
        self._plan_store = None

    @property
    def plan_store(self):
        if self._plan_store is None:
            from easel.video_production_plans import VideoProductionPlanStore
            self._plan_store = VideoProductionPlanStore(self.directory.parent / 'video-production-plans',
                workspace=self.outputs.parent, repository=self.repository, actor_id=self.actor_id,
                ip=self.ip, session_id=self.session_id, authorize=self.authorize)
        return self._plan_store

    def _has_plan(self):
        path = self.directory.parent / 'video-production-plans' / 'video_production_plans.sqlite3'
        return path.exists() and self.plan_store.has_plan()

    def _safe(self, path: Path):
        path.relative_to(self.outputs)
        if any(item.is_symlink() for item in (path, *path.parents) if item.is_relative_to(self.outputs)):
            raise WorkshopError("视频操作目录不能经过符号链接", 403)

    def _private(self, path: Path):
        path.relative_to(self.directory)
        if (self.directory.resolve().is_relative_to(self.outputs.parent)
                or any(item.is_symlink() for item in (path, *path.parents) if item.is_relative_to(self.directory))):
            raise WorkshopError("Host 视频权威回执目录不可用", 403)

    def _catalog(self):
        self.authorize()
        return self.client.catalog()

    def _validate_spec(self, spec, project, models=None):
        target_dimensions(spec)
        model = next((item for item in (models if models is not None else self._catalog())
                      if item['modelId'] == spec['modelId']), None)
        refs = spec.get('references', [])
        if not model or not (model['textToVideo'] if not refs else
                             model.get('imageToVideo') or model.get('referenceToVideo')):
            raise WorkshopError('请选择当前 IP 已配置、支持本次输入的生成模型', 422)
        if model.get('profileNeedsRepair'):
            raise WorkshopError('Ref11 模型目录须先按真实参考与原生规格修复，尚未提交生成', 422)
        seconds, duration = spec['seconds'], model['duration']
        if (spec['aspectRatio'] not in model['ratios'] or spec['aspectRatio'] != project['aspect_ratio']
                or spec['resolution'] not in model['resolutions']
                or not duration.get('min', 1) <= seconds <= duration.get('max', 0)
                or (seconds - duration.get('min', 1)) % max(1, duration.get('step', 1))
                or duration.get('options') and seconds not in duration['options']):
            raise WorkshopError('制作参数不符合当前模型能力或工坊画幅', 422)
        if refs:
            from easel.video_generation_references import validate_reference_spec
            validate_reference_spec(spec['modelId'], seconds, spec['aspectRatio'], refs)

    def _prepare_plan(self, payload, project):
        path = self.outputs / payload['manifestPath']
        self._safe(path)
        if not path.is_file() or path.stat().st_size > 256 * 1024:
            raise WorkshopError('当前 IP 的制作清单不可用或过大', 422)
        raw = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(raw, dict) or not isinstance(raw.get('clips'), list)
                or not all(isinstance(clip, dict) for clip in raw['clips'])):
            raise WorkshopError('制作计划须列出固定生产片段', 422)
        supersedes_plan_id = raw.get('supersedesPlanId')
        if 'supersedesPlanId' in raw:
            if not isinstance(supersedes_plan_id, str) or re.fullmatch(r'[a-f0-9]{32}', supersedes_plan_id) is None:
                raise WorkshopError('待替换制作计划编号无效', 422)
            raw = {key: value for key, value in raw.items() if key != 'supersedesPlanId'}
        from easel.video_production_plans import normalize_production_plan
        # Check the bounded public shape before probing any media or models.
        # Reference evidence is supplied only by the Host in the second pass.
        normalize_production_plan({**raw, 'clips': [{**clip, 'references': []} for clip in raw['clips']]})
        collected_claims = None
        if supersedes_plan_id is not None:
            parent = self.plan_store.get(supersedes_plan_id, workshop_id=project['id'])
            if parent['state'] not in {'approved', 'superseded'}:
                raise WorkshopError('只能替换已批准计划中尚未提交的片段', 409)
            if {clip['clipId'] for clip in raw['clips']} & {clip['clipId'] for clip in parent['clips']}:
                raise WorkshopError('替换计划须使用新的片段编号，不能覆盖原回执', 409)
            collected_claims = self._collected_parent_claims(parent, project)
        normalized = dict(raw)
        clips = []
        models = self._catalog()
        for clip in raw['clips']:
            if not isinstance(clip, dict):
                raise WorkshopError('制作片段字段无效', 422)
            refs = clip.get('references', [])
            if not isinstance(refs, list):
                raise WorkshopError('参考素材须为明确列表', 422)
            if refs:
                from easel.video_generation_references import freeze_references
                refs = freeze_references(self.outputs, refs)
            frozen = {**clip, 'references': refs}
            spec = {key: raw.get(key) for key in ('modelId', 'aspectRatio', 'resolution')}
            spec.update({key: frozen.get(key) for key in ('seconds', 'prompt', 'references')})
            # Structural validation precedes all draft/model operations.
            clips.append(frozen)
            self._validate_spec(spec, project, models)
        normalized['clips'] = clips
        normalized = normalize_production_plan(normalized)
        if supersedes_plan_id is None:
            plan = self.plan_store.prepare(project['id'], normalized)
            self._sync_plan_project(project, normalized)
            return plan
        plan = self.plan_store.prepare(project['id'], normalized,
            supersedes_plan_id=supersedes_plan_id, collected_claims=collected_claims)
        self._check_replacement_records(plan, project)
        self._sync_plan_project(project, normalized)
        return plan

    def _sync_plan_project(self, project, plan):
        assets = [dict(row) for row in project['assets']]
        by_digest = {}
        for asset in assets:
            if asset.get('sha256'):
                by_digest.setdefault(asset['sha256'], []).append(asset['id'])
        shots = {row['id']: {**row, 'asset_ids': list(row['asset_ids'])} for row in project['shots']}
        for clip in plan['clips']:
            identifier = clip.get('shotId', clip['clipId'])
            # A plan freezes candidate inputs, not a replacement for an edited
            # storyboard. Retakes for one shot must not overwrite its main view.
            if identifier not in shots:
                shots[identifier] = {'id': identifier, 'title': identifier,
                    'prompt': clip['prompt'], 'seconds': clip['seconds'], 'asset_ids': []}
            bound = shots[identifier]['asset_ids']
            for reference in clip['references']:
                matching = by_digest.get(reference['sha256'])
                if not matching:
                    asset_id = 'ref-' + reference['sha256'][:16]
                    assets.append({'id': asset_id, 'kind': reference['kind'],
                        'name': Path(reference['path']).stem[:120], 'description': '', 'path': reference['path']})
                    matching = by_digest[reference['sha256']] = [asset_id]
                # One image can deliberately represent several named roles.
                # Keep explicit bindings; never arbitrarily replace them with
                # the last asset having the same bytes.
                if not any(asset_id in bound for asset_id in matching):
                    bound.extend(matching)
        self.store.production(project['id'], project['revision'],
            {'raw_content': project['raw_content'], 'script': project['script'],
             'assets': assets, 'shots': list(shots.values())})

    def _collected_parent_claims(self, parent, project):
        """Build replacement evidence exclusively from private Host collection records."""
        evidence = []
        for claim in parent['clipClaims']:
            path = self.directory / (project['id'] + '-' + claim['clipId'] + '.json')
            record = self._read(path, project, {'clipId': claim['clipId']})
            if (record is None or record.get('sessionId') != self.session_id
                    or record.get('planId') != parent['planId']
                    or record.get('operationId') != claim['operationId']
                    or record.get('submitAttempted') is not True
                    or record.get('status') != 'collected'
                    or record.get('receiptType') != 'production_segment'
                    or not isinstance(record.get('taskId'), str)
                    or re.fullmatch(_H3_JOB, record['taskId']) is None):
                raise WorkshopError('原计划已提交片段尚未核实真实收取，只能查询原任务', 409)
            artifact = record.get('artifact')
            if (not isinstance(artifact, dict) or artifact.get('kind') != 'production_segment'
                    or artifact.get('id') != record.get('id')):
                raise WorkshopError('原计划片段的收取身份不完整，不能替换制作范围', 409)
            self._verify_collected_snapshot(artifact)
            self._verify_collected_snapshot(record.get('sourceFile'))
            evidence.append({'clipId': claim['clipId'], 'operationId': claim['operationId'],
                'taskId': record['taskId'], 'artifactSha256': artifact['sha256']})
        return evidence

    def _verify_collected_snapshot(self, value):
        if (not isinstance(value, dict) or not isinstance(value.get('path'), str)
                or not isinstance(value.get('sha256'), str)
                or re.fullmatch(r'[a-f0-9]{64}', value['sha256']) is None):
            raise WorkshopError('原计划已收取视频的文件证据不完整', 409)
        relative = PurePosixPath(value['path'])
        if (not value['path'] or len(value['path']) > 1024 or '\\' in value['path']
                or ':' in value['path'] or relative.is_absolute() or str(relative) != value['path']
                or any(part in {'.', '..'} or part.startswith('.') for part in relative.parts)):
            raise WorkshopError('原计划已收取视频路径不属于当前 IP 内容目录', 409)
        path = self.outputs / relative
        self._safe(path)
        if not path.is_file() or _digest(path) != value['sha256']:
            raise WorkshopError('原计划已收取视频的文件版本已变化，请保留原回执排查', 409)

    def _check_replacement_records(self, plan, project):
        for clip in plan['clips']:
            path = self.directory / (project['id'] + '-' + clip['clipId'] + '.json')
            record = self._read(path, project, {'clipId': clip['clipId']})
            if record is not None and (record.get('sessionId') != self.session_id
                    or record.get('planId') != plan['planId']):
                raise WorkshopError('替换片段编号已有其他私有回执，不能覆盖原任务', 409)

    def plan_decision(self, workshop_id, plan_id, scope_hash, approved):
        """Only the authenticated Host calls this after its manual question."""
        self.authorize()
        project = self._owned_project(workshop_id)
        plan = self.plan_store.get(plan_id, workshop_id=workshop_id)
        if approved is True:
            for clip in plan['clips']:
                self._verify_references(clip)
            if plan.get('supersession') and plan['state'] == 'pending':
                parent = self.plan_store.get(plan['supersession']['planId'], workshop_id=workshop_id)
                evidence = self._collected_parent_claims(parent, project)
                expected = [{key: item[key] for key in ('clipId', 'operationId', 'taskId', 'artifactSha256')}
                            for item in plan['supersession']['collectedClaims']]
                if evidence != expected:
                    raise WorkshopError('原计划已提交片段已变化，请重新核对替换范围', 409)
                self._check_replacement_records(plan, project)
        return self.plan_store.decide(plan_id, scope_hash, approved)

    def plan_reconfirmation(self, workshop_id, plan_id):
        """Host-only preflight for a new human card about an unchanged expired scope."""
        self.authorize()
        self._owned_project(workshop_id)
        plan = self.plan_store.reconfirmation(plan_id, workshop_id=workshop_id)
        for clip in plan['clips']:
            self._verify_references(clip)
        return plan

    def plan_reconfirmation_decision(self, workshop_id, plan_id, scope_hash, expired_at, approved):
        self.authorize()
        self._owned_project(workshop_id)
        plan = self.plan_store.get(plan_id, workshop_id=workshop_id)
        if approved is True:
            for clip in plan['clips']:
                self._verify_references(clip)
        return self.plan_store.reconfirm(plan_id, scope_hash, expired_at, approved, workshop_id=workshop_id)

    def _verify_references(self, spec):
        refs = spec.get('references', [])
        if refs:
            from easel.video_generation_references import verify_references
            verify_references(self.outputs, refs)

    def _owned_project(self, identifier):
        self._safe(self.store.directory / (identifier + ".json"))
        project = self.store.get(identifier)
        if project.get("archived_at"):
            raise WorkshopError("当前视频工坊已归档，请恢复后继续制作", 409)
        return project

    def _read(self, path, project, payload):
        self._private(path)
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 128 * 1024:
                raise ValueError
            record = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(record, dict) or record.get("version") != 2
                    or record.get("workshopId") != project["id"] or record.get("clipId") != payload["clipId"]
                    or record.get("actorId") != self.actor_id or record.get("ip") != self.ip):
                raise ValueError
        except (OSError, ValueError):
            raise WorkshopError("视频回执不属于当前用户、IP、工坊或原片段，请保留记录排查", 403) from None
        return record

    def _save(self, path, record):
        self.authorize()
        self._private(path)
        descriptor, temporary = tempfile.mkstemp(dir=self.directory, prefix=".video-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _public(self, record):
        keys = ("workshopId", "clipId", "status", "shotId", "taskId", "spec", "receiptType",
                "artifact", "media",
                "sourceMedia", "normalization", "hostTaskStatus", "providerStatus", "providerObservedAt", "planId")
        value = {**{key: record[key] for key in keys if key in record},
                 "workshopUrl": f"/#/business/{quote(self.ip, safe='')}/workshop/{record['workshopId']}"}
        if record.get("artifact"):
            value["workspacePath"] = "outputs/" + record["artifact"]["path"]
        if record.get("sourceFile"):
            value["sourceWorkspacePath"] = "outputs/" + record["sourceFile"]["path"]
        return value

    def _budget(self, record):
        if record.get('planId'):
            spec = self.plan_store.approved_clip(record['planId'], record['clipId'], workshop_id=record['workshopId'])
            expected = {key: spec[key] for key in (*_SPEC, 'references')}
            if expected != record['spec']:
                raise WorkshopError('片段内容与人工批准计划不一致，不会提交', 409)
            return
        if self._has_plan():
            raise WorkshopError('本会话已进入计划制作，请使用已批准计划中的固定片段，不会额外生成', 409)
        for path in self.directory.glob("*.json"):
            self._private(path)
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise WorkshopError("已有视频提交记录无法核实，不会创建新付费任务", 409) from None
            if (item.get("sessionId") == self.session_id and item.get("actorId") == self.actor_id
                    and item.get("submitAttempted")
                    and item.get("id") != record["id"]):
                raise WorkshopError("本次 AI 会话已提交一条视频，请查询原任务；制作另一条须另开明确任务", 409)

    @staticmethod
    def _shot_snapshot(project, shot_id):
        shot = next((row for row in project['shots'] if row['id'] == shot_id), None)
        if shot is None:
            return None
        return {**{key: shot[key] for key in ('id', 'prompt', 'seconds', 'asset_ids')},
            'assets': [{key: asset.get(key) for key in ('id', 'sha256')} for asset in project['assets']
                       if asset['id'] in shot['asset_ids']]}

    def _prepare(self, payload, project, path, record):
        plan_id = payload.get('planId')
        if plan_id:
            plan_clip = self.plan_store.approved_clip(plan_id, payload['clipId'], workshop_id=project['id'])
            spec = {key: plan_clip[key] for key in (*_SPEC, 'references')}
        else:
            spec = {key: payload.get(key, '720p' if key == 'resolution' else None) for key in _SPEC}
            spec['references'] = []
        self._validate_spec(spec, project)
        self._verify_references(spec)
        shot_id = plan_clip.get('shotId', payload['clipId']) if plan_id else payload.get('shotId', payload['clipId'])
        if plan_id and payload.get('shotId', shot_id) != shot_id:
            raise WorkshopError('返工分镜必须与批准计划一致', 409)
        if record:
            if record.get('shotId') != shot_id or record['spec'] != spec or record.get('planId') != plan_id:
                raise WorkshopError('原片段已绑定不同制作参数，请创建新的候选任务', 409)
            return self._public(record)
        shot = next((row for row in project['shots'] if row['id'] == shot_id), None)
        if shot is None:
            values = {'script': project['script'],
                'assets': [{key: row[key] for key in ('id', 'kind', 'name', 'description', 'path') if key in row}
                           for row in project['assets']],
                'shots': [{key: row[key] for key in ('id', 'title', 'prompt', 'seconds', 'asset_ids')} for row in project['shots']]}
            values['shots'].append({'id': shot_id, 'title': payload['clipId'], 'prompt': spec['prompt'],
                                   'seconds': spec['seconds'], 'asset_ids': []})
            project = self.store.production(project['id'], project['revision'], values)
        identifier = hashlib.sha256((project['id'] + '\n' + payload['clipId']).encode()).hexdigest()[:32]
        record = {'version': 2, 'id': identifier, 'actorId': self.actor_id, 'ip': self.ip,
            'sessionId': self.session_id, 'workshopId': project['id'], 'clipId': payload['clipId'],
            'shotId': shot_id, 'shotSnapshot': self._shot_snapshot(project, shot_id), 'spec': spec, 'status': 'prepared', 'operationId': 'easel-video:' + identifier,
            'receiptType': 'production_segment' if plan_id else 'generation'}
        if plan_id:
            record['planId'] = plan_id
        self._budget(record)
        self._save(path, record)
        return self._public(record)

    def _propose(self, record):
        self._budget(record)
        project = self._owned_project(record['workshopId'])
        if not record.get('shotSnapshot') or self._shot_snapshot(project, record['shotId']) != record['shotSnapshot']:
            raise WorkshopError('该镜头或参考素材已被修改，未提交旧生成请求，请读取当前版本', 409)
        self._validate_spec(record['spec'], project)
        self._verify_references(record['spec'])
        return {'operationId': record['operationId'], 'modelId': record['spec']['modelId']}

    def _observe(self, record, path, **_):
        if record['status'] == 'collected' or not record.get('taskId'):
            return
        self.authorize()
        task = self.client.status(record['spec']['modelId'], record['taskId'])
        if task.get('id') != record['taskId']:
            raise WorkshopError('原视频任务身份已变化，未采用其他任务', 409)
        state = {'completed': 'succeeded', 'in_progress': 'running', 'processing': 'running'}.get(task.get('status'), task.get('status'))
        if state not in {'queued', 'running', 'succeeded', 'failed', 'cancelled', 'unknown'}:
            raise WorkshopError('原视频任务状态无法核对', 409)
        record['status'] = 'unconfirmed' if state == 'unknown' else state
        self._save(path, record)

    def _submit(self, record, path):
        if record.get('submitAttempted'):
            self._observe(record, path)
            return self._public(record)
        if record.get('sessionId') != self.session_id or record['status'] != 'prepared':
            raise WorkshopError('请先在当前会话准备这份制作任务', 409)
        self._propose(record)
        if record.get('planId'):
            claimed = self.plan_store.claim_clip(record['planId'], record['clipId'], record['operationId'],
                                                 workshop_id=record['workshopId'])
            if not claimed['claimed']:
                raise WorkshopError('原计划已存在提交意图，只能查询原任务', 409)
        record.update(submitAttempted=True, status='unconfirmed')
        self._save(path, record)
        try:
            self.authorize()
            result = self.client.submit(record['spec'], record['operationId'], self.outputs)
            task_id = result.get('id')
            if not isinstance(task_id, str) or not re.fullmatch(_H3_JOB, task_id):
                raise ValueError
            record['taskId'] = task_id
            self._save(path, record)
        except WorkshopError:
            return self._public(record)
        except (ValueError, TypeError):
            return self._public(record)
        self._observe(record, path)
        return self._public(record)

    def _collect(self, record, path, project):
        if record['status'] == 'collected':
            self._verify_collected_snapshot(record['artifact'])
            return self._public(record)
        self._observe(record, path)
        if record['status'] != 'succeeded':
            raise WorkshopError('原视频任务尚未成功，不能收取', 409)
        directory = self.outputs / ('视频生成-' + project['id'][:8]) / 'assets'
        self._safe(directory)
        directory.mkdir(parents=True, exist_ok=True)
        original = directory / ('source-' + record['id'] + '.mp4')
        destination = directory / (record['id'] + '.mp4')
        self.authorize()
        self.client.download(record['spec']['modelId'], record['taskId'], original)
        source_media = probe_generated_video(original, record['spec'], source=True)
        if (source_media['width'], source_media['height']) == target_dimensions(record['spec']):
            destination, media, normalization = original, source_media, 'native_dimensions'
        else:
            media = normalize_generated_video(original, destination, record['spec'])
            normalization = 'local_ffmpeg'
        record.update(sourceFile={'path': original.relative_to(self.outputs).as_posix(), 'sha256': _digest(original)},
                      sourceMedia=source_media, normalization=normalization)
        relative = destination.relative_to(self.outputs).as_posix()
        current = self.store.get(project['id'])
        unchanged_shot = self._shot_snapshot(current, record['shotId']) == record.get('shotSnapshot')
        current = self.store.add_candidate(project['id'], current['revision'], record['shotId'], relative,
            candidate_id=record['id'], task_id=record['taskId'],
            source_prompt=record['spec']['prompt'], auto_select=unchanged_shot)
        shot = next(row for row in current['shots'] if row['id'] == record['shotId'])
        artifact = next(row for row in shot['candidates'] if row['id'] == record['id'])
        if record.get('planId'):
            artifact = {**artifact, 'kind': 'production_segment'}
        elif len(current['shots']) == 1 and unchanged_shot and shot['selected_candidate_id'] == record['id']:
            # A single-shot request is already a complete film. Multi-shot plans
            # remain candidates until the Skill masters and delivers the film.
            existing = next((row for row in current['deliverables'] if row['sha256'] == artifact['sha256']
                             and row['revision'] == current['revision']), None)
            if not existing:
                current = self.store.deliver(project['id'], current['revision'], artifact['path'])
                existing = current['deliverables'][-1]
            artifact = existing
        record.update(artifact=artifact, media=media, status='collected')
        self._save(path, record)
        return self._public(record)

    def run(self, payload):
        self.authorize()
        payload = video_generation_payload(payload)
        if payload["action"] == "catalog":
            return {"models": self._catalog(), "generationSubmitted": False}
        self._private(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _file_lock(self.directory / "writer.lock", blocking=False) as acquired:
            if not acquired:
                raise WorkshopError("当前 IP 正在处理视频制作回执，请查询原任务", 409)
            project = self._owned_project(payload["workshopId"])
            if payload['action'] in _PLAN_ACTIONS:
                if payload['action'] == 'plan':
                    return self._prepare_plan(payload, project)
                return self.plan_store.get(payload['planId'], workshop_id=project['id'])
            path = self.directory / (payload["workshopId"] + "-" + payload["clipId"] + ".json")
            record = self._read(path, project, payload)
            action = payload["action"]
            if action == "prepare":
                return self._prepare(payload, project, path, record)
            if record is None:
                raise WorkshopError("当前 IP 未找到已准备的原视频片段", 404)
            if action == "propose":
                return {**self._public(record), "source": self._propose(record), "generationSubmitted": False}
            if action == "submit":
                return self._submit(record, path)
            if action == "collect":
                return self._collect(record, path, project)
            if record.get("submitAttempted"):
                self._observe(record, path, refresh_failed=True)
            return self._public(record)
