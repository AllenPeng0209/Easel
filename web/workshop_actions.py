"""Workbench buttons run the existing authenticated chat/Skill Host in the background."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from fastapi import HTTPException
from easel.host_state import generation_state_directory
from easel.projects import project_id
from easel.request_identity import current_user
from easel.video_workshop import WorkshopError, WorkshopStore

_QUEUES = {}

LABELS = {'rewrite': 'AI 改写', 'extract_assets': '提取角色、场景与道具', 'image_prompts': '生成图片提示词',
          'generate_assets': '生成资产图片', 'breakdown': '重新拆分分镜', 'video_prompts': '补充视频提示词',
          'generate_video': '生成分镜视频'}
INSTRUCTIONS = {
    'rewrite': '读取原始内容，将其改写成可拍摄的完整剧本，只更新 raw_content 和 script，保留资产和分镜。',
    'extract_assets': '从当前剧本提取并去重角色、场景、道具，保存到 assets。使用稳定 ID，不删除已有人物和素材版本。不要生成视频。',
    'image_prompts': '只为所选资产生成 image_prompt 图片提示词，参考描述、外观与造型，保持其余字段和历史素材不变。',
    'generate_assets': '调用生图 Skill 为所选资产生成真实图片，使用当前图片模型；保存文件后更新对应资产 path。保留全部旧素材和其他资产。',
    'breakdown': '将当前剧本拆成完整连续分镜。每镜分别填写 description、atmosphere、prompt、seconds、asset_ids。prompt 是视频提示词，description 是画面描述，不要把两者混成一个字段。尽量复用稳定镜头 ID，保留既有候选。',
    'video_prompts': '只为所选镜头生成或补充 prompt 视频提示词。依据 description、atmosphere、seconds 和绑定资产，用 @素材名 引用，分时间段写画面、动作、运镜、对白。不得改其他字段或重新拆分整集。',
    'generate_video': '仅为所选镜头调用 easel_video_generation。先读取模型目录，制作冻结计划；每个新候选用唯一 clipId，并用 shotId 绑定原分镜。参考必须使用本项目已绑定资产的实际文件。保留其他镜头和候选，成功后 collect 回同一工坊。费用和计算范围沿用既有授权，新的生成额度通过 Host 制作计划确认，不能伪造人工批准。结果未知时只查原任务，不自动购买第二次。',
}


class WorkshopActions:
    def __init__(self, host):
        self.host = host

    def scope(self):
        self.host._require_host_tools()
        actor = current_user()
        if not actor:
            raise HTTPException(401, '请先登录')
        return actor['id'], project_id()

    @contextmanager
    def database(self):
        _, ip = self.scope()
        folder = generation_state_directory(self.host.PROJECT_ROOT, ip).parent / 'workshop-actions'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(folder / 'actions.sqlite3', timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, actor TEXT NOT NULL, ip TEXT NOT NULL, '
                   'workshop TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, data TEXT NOT NULL, '
                   'UNIQUE(actor,ip,request_id))')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _save(self, row):
        actor, ip = self.scope()
        with self.database() as db:
            db.execute('UPDATE actions SET data=? WHERE id=? AND actor=? AND ip=?',
                       (json.dumps(row, ensure_ascii=False), row['id'], actor, ip))

    def _read(self, identifier, job_id):
        actor, ip = self.scope()
        with self.database() as db:
            value = db.execute('SELECT data FROM actions WHERE id=? AND actor=? AND ip=? AND workshop=?',
                               (job_id, actor, ip, identifier)).fetchone()
        if not value:
            raise HTTPException(404, '制作任务不存在')
        return json.loads(value[0])

    @staticmethod
    def _evidence(project, action):
        if action == 'rewrite':
            return project['script']
        if action in {'extract_assets', 'generate_assets', 'image_prompts'}:
            return project['assets']
        return project['shots']

    async def observe(self, row):
        self.scope()
        active = row['turn_id'] in self.host._ACTIVE_TURN_IDS
        if not active and row.get('finished_at') and row['status'] in {'completed', 'failed', 'cancelled'}:
            # A durable result must not be reclassified by later manual edits,
            # nor should every status poll decode all historical tool output.
            return self._public(row)
        events = await asyncio.to_thread(self.host._read_job_events, row['turn_id'])
        questions = {}
        for event in events:
            kind, value = event.get('event'), event.get('data')
            if kind in {'question', 'question_closed'}:
                try:
                    value = json.loads(value) if isinstance(value, str) else value
                    if not isinstance(value, dict):
                        continue
                    if kind == 'question':
                        questions[value['id']] = value
                    else:
                        questions.pop(value.get('id'), None)
                except (ValueError, KeyError):
                    continue
            elif kind == 'activity' and isinstance(value, str):
                row['message'] = self.host._scrub_secrets(value)[:500]
            elif kind == 'error':
                row.update(status='failed', message='AI 制作尚未完成，请查看任务详情后重试。')
            elif kind == 'done':
                outcome = value.get('outcome') if isinstance(value, dict) else None
                state = {'completed': 'completed', 'failed': 'failed', 'stopped': 'cancelled'}.get(outcome, 'unconfirmed')
                row.update(status=state)
                if event.get('id'): row.setdefault('finished_at', int(time.time() * 1000))
        active = row['turn_id'] in self.host._ACTIVE_TURN_IDS
        if active:
            row['status'] = 'waiting_input' if questions else 'running'
        elif not row.get('waiting_queue') and row['status'] in {'queued', 'running', 'waiting_input'} and time.time() * 1000 - row['created_at'] > 5000:
            row.update(status='unconfirmed', message='原任务状态待核对，未重复提交。')
        if row['status'] == 'completed':
            current = WorkshopStore(self.host._outputs_dir()).get(row['workshop_id'])
            if self._evidence(current, row['action']) == row['before']:
                row.update(status='failed', message='AI 已结束，但没有保存本次要求的制作结果。')
        row['questions'] = list(questions.values()) if active else []
        await asyncio.to_thread(self._save, row)
        return self._public(row)

    @staticmethod
    def _public(row):
        return {key: row[key] for key in ('id', 'workshop_id', 'action', 'label', 'status', 'session_id', 'turn_id',
                'shot_ids', 'asset_ids', 'created_at', 'questions', 'message', 'finished_at') if key in row}

    async def list(self, identifier):
        actor, ip = self.scope()
        WorkshopStore(self.host._outputs_dir()).get(identifier)
        with self.database() as db:
            rows = [json.loads(row[0]) for row in db.execute(
                'SELECT data FROM actions WHERE actor=? AND ip=? AND workshop=? ORDER BY rowid DESC LIMIT 60',
                (actor, ip, identifier))]
        if any(row.get('waiting_queue') for row in rows): self._kick(identifier)
        return [await self.observe(row) for row in rows]

    async def submit(self, identifier, payload, *, queued=False):
        actor, ip = self.scope()
        fingerprint = hashlib.sha256(json.dumps([identifier, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.database() as db:
            prior = db.execute('SELECT fingerprint,data FROM actions WHERE actor=? AND ip=? AND request_id=?',
                               (actor, ip, payload['request_id'])).fetchone()
        if prior:
            if prior[0] != fingerprint:
                raise HTTPException(409, '同一操作编号已绑定不同制作内容')
            return await self.observe(json.loads(prior[1]))
        store = WorkshopStore(self.host._outputs_dir())
        project = store.get(identifier)
        try:
            store._expect(project, payload['expected_revision'])
        except WorkshopError as error:
            raise HTTPException(error.status, str(error)) from None
        if project.get('archived_at'):
            raise HTTPException(409, '请先恢复归档项目')
        action = payload['action']
        shot_ids, asset_ids = payload['shot_ids'], payload['asset_ids']
        if (len(set(shot_ids)) != len(shot_ids) or set(shot_ids) - {row['id'] for row in project['shots']}
                or len(set(asset_ids)) != len(asset_ids) or set(asset_ids) - {row['id'] for row in project['assets']}):
            raise HTTPException(422, '只能操作当前项目的分镜和素材')
        if action in {'breakdown', 'extract_assets'} and not project['script'].strip():
            raise HTTPException(422, '请先保存拍摄剧本')
        if action in {'generate_video', 'video_prompts'}:
            if not shot_ids:
                shot_ids = [row['id'] for row in project['shots'] if not (row['selected_candidate_id'] if action == 'generate_video' else row['prompt'].strip())]
            if not shot_ids:
                raise HTTPException(422, '没有待处理镜头，请先选择需要重做的分镜')
            if action == 'generate_video' and any(not row['prompt'].strip() for row in project['shots'] if row['id'] in shot_ids):
                raise HTTPException(422, '所选镜头缺少视频提示词，请先补全')
        if action in {'generate_assets', 'image_prompts'}:
            if not asset_ids:
                asset_ids = [row['id'] for row in project['assets'] if not row.get('path' if action == 'generate_assets' else 'image_prompt')]
            if not asset_ids:
                raise HTTPException(422, '没有待处理素材，请先选择需要重新制作的资产')
        runtime = self.host._chat_backend(None)
        if runtime == 'api':
            raise HTTPException(409, '当前底座仅支持聊天，请在设置中选择可调用 Skill 的 Agent 底座')
        jobs = await self.list(identifier)
        if not queued and any(row['status'] in {'queued', 'running', 'waiting_input', 'unconfirmed'} for row in jobs):
            raise HTTPException(409, '本项目已有制作任务，请在任务面板继续原任务，避免重复生成')
        job_id = uuid.uuid4().hex
        row = {'id': job_id, 'workshop_id': identifier, 'action': action, 'label': LABELS[action], 'status': 'queued',
            'request_id': payload['request_id'], 'session_id': 'workshop-action-' + job_id, 'turn_id': uuid.uuid4().hex,
            'created_at': int(time.time() * 1000), 'shot_ids': shot_ids, 'asset_ids': asset_ids,
            'message': '已接收制作任务', 'questions': [], 'before': self._evidence(project, action),
            'waiting_queue': queued, 'payload': payload, 'input_script': project['script']}
        with self.database() as db:
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?)', (job_id, actor, ip, identifier,
                payload['request_id'], fingerprint, json.dumps(row, ensure_ascii=False)))
        if queued:
            self._kick(identifier)
            return await self.observe(row)
        return await self._launch(row, project, payload)

    async def _launch(self, row, project, payload):
        identifier, action = row['workshop_id'], row['action']
        shot_ids, asset_ids = row['shot_ids'], row['asset_ids']
        prompt = ('请调用 video-workshop 与对应制作 Skill，执行用户在视频工作台点选的特定工作。\n'
            f"任务：{LABELS[action]}。\n{INSTRUCTIONS[action]}\n"
            f"只处理分镜：{json.dumps(shot_ids, ensure_ascii=False)}；只处理资产：{json.dumps(asset_ids, ensure_ascii=False)}。\n"
            f"用户补充：{payload['notes']}\n"
            f"模型与交付规格：{json.dumps(project.get('studio', {}), ensure_ascii=False)}\n"
            f"工坊原始内容与剧本、资产、分镜：{json.dumps({key: project[key] for key in ('raw_content','script','assets','shots')}, ensure_ascii=False)}\n"
            '编辑时先读取最新工坊，保护人工修改。将更新清单写在 outputs/<本片>/workbench-edit.json，格式为 '
            f'{{"workshopId":"{identifier}","expectedRevision":<当前真实版本>,"production":{{"raw_content":...,"script":...,"assets":[...],"shots":[...]}}}}。'
            '调用 easel_workshop_delivery(action="update",manifestPath="<本片>/workbench-edit.json") 保存回同一项目。'
            '候选与版本由 Host 保留，不要直接改写工坊数据库。不得调用发布工具，不要发布或伪造人工审核；发布只由工坊最终审核触发。')
        try:
            response = await self.host.api_chat_stream(self.host.ChatRequest(message=prompt, sessionId=row['session_id'],
                turnId=row['turn_id'], workshopRef=self.host.ChatWorkshopRef(id=identifier, revision=project['revision']),
                modelOverride=project.get('studio', {}).get('text_model') or None))
            if hasattr(response.body_iterator, 'aclose'):
                await response.body_iterator.aclose()
        except BaseException:
            row.update(status='unconfirmed' if row['turn_id'] in self.host._ACTIVE_TURN_IDS else 'failed',
                       message='制作任务未取得明确回执，请查看原任务状态。')
            self._save(row)
            raise
        return await self.observe(row)

    def _kick(self, identifier):
        actor, ip = self.scope()
        key = (str(self.host.PROJECT_ROOT), actor, ip, identifier)
        if key in _QUEUES and not _QUEUES[key].done():
            return
        async def pump():
            while True:
                with self.database() as db:
                    rows = [json.loads(row[0]) for row in db.execute('SELECT data FROM actions WHERE actor=? AND ip=? AND workshop=? ORDER BY rowid', (actor, ip, identifier))]
                pending = next((row for row in rows if row.get('waiting_queue')), None)
                if pending is None:
                    return
                current = WorkshopStore(self.host._outputs_dir()).get(identifier)
                pending['waiting_queue'] = False
                if current['script'] != pending['input_script']:
                    pending.update(status='failed', message='等待期间剧本已变更，未执行旧版本制作请求')
                    self._save(pending)
                    continue
                pending['before'] = self._evidence(current, pending['action'])
                self._save(pending)
                try:
                    await self._launch(pending, current, pending['payload'])
                    while pending['turn_id'] in self.host._ACTIVE_TURN_IDS:
                        self.scope()
                        await asyncio.sleep(.1)
                    await self.observe(pending)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pending.update(status='failed', message='原制作步骤未完成，请查询原任务')
                    self._save(pending)
        task = asyncio.create_task(pump())
        _QUEUES[key] = task
        self.host._BG_TASKS.add(task)
        task.add_done_callback(self.host._BG_TASKS.discard)

    async def stop(self, identifier, job_id):
        row = self._read(identifier, job_id)
        return await self.host.api_chat_stop(self.host.StopRequest(sessionId=row['session_id'], expectedTurnId=row['turn_id']))
