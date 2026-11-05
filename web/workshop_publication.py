"""Bind a native film review to the existing Host publication authority."""
from fastapi import HTTPException
from easel.video_workshop import WorkshopError


class WorkshopPublication:
    def __init__(self, store, workflow):
        self.store, self.workflow = store, workflow

    def _current(self, identifier, revision, artifact_id):
        from web.huobao_bridge import reconcile
        reconcile(self.workflow.host, identifier)
        project = self.store.get(identifier)
        self.store._expect(project, revision)
        artifact = self.store._reviewable_artifact(project, artifact_id)
        if project.get('archived_at'):
            raise WorkshopError('项目已归档，请恢复后审核', 409)
        return project, artifact

    async def prepare(self, identifier, data):
        project, artifact = self._current(identifier, data['expected_revision'], data['artifact_id'])
        payload = {key: value for key, value in data.items() if key not in {'expected_revision', 'artifact_id', 'platform'}}
        payload['media'] = [artifact['path']]
        approval = await self.workflow.prepare(data['platform'], payload)
        project = self.store.bind_publication(identifier, project['revision'], artifact['id'], approval)
        return {'project': project, 'approval': approval}

    def _http_authority(self, platform):
        from web.publication_operator_guard import require_operator_http_available
        scope = self.workflow.scope()
        require_operator_http_available(self.workflow.host._publication_dir(),
            actor_id=scope['actor_id'], platform=platform)

    async def review(self, identifier, data):
        project, artifact = self._current(identifier, data['expected_revision'], data['artifact_id'])
        values = {key: data[key] for key in ('kind', 'decision', 'notes')}
        values['actor_id'] = self.workflow.scope()['actor_id']
        binding = project.get('publication_preparation')
        if values['kind'] != 'human' or values['decision'] != 'approved' or not binding:
            return self.store.review(identifier, project['revision'], artifact['id'], values)
        if (data.get('publication_approval_id') != binding['approval_id']
                or data.get('publication_payload_hash') != binding['payload_hash']
                or binding['revision'] != project['revision'] or binding['artifact_id'] != artifact['id']
                or binding['artifact_sha256'] != artifact['sha256']):
            raise WorkshopError('请先核对当前成片、文案和账号，再审核通过并发布', 409)
        record = self.workflow.get(binding['approval_id'])
        if record['actorId'] != binding['actor_id'] or record['payloadHash'] != binding['payload_hash']:
            raise WorkshopError('发布对象已变化，请重新准备发布信息', 409)
        platform = record['subject']['platform']['id']
        self._http_authority(platform)
        self.workflow.decide(record['id'], True, record['payloadHash'])
        self.store.review(identifier, project['revision'], artifact['id'], values)

        def verify_dispatch():
            self._http_authority(platform)
            current, checked = self._current(identifier, binding['revision'], binding['artifact_id'])
            if current.get('publication_preparation') != binding or checked['sha256'] != binding['artifact_sha256']:
                raise HTTPException(409, '审核期间内容或发布信息发生变化，未提交旧版本')
            latest = next((row for row in reversed(current['reviews']) if row['artifact_id'] == artifact['id']
                           and row['revision'] == binding['revision']), None)
            if not latest or latest['kind'] != 'human' or latest['decision'] != 'approved':
                raise HTTPException(409, '当前成片未通过人工审核')

        payload = {key: record['subject'].get(key, '') for key in ('title', 'body', 'tags', 'persona')}
        payload.update(aiGenerated=record['subject'].get('aiGenerated', False),
            artifactIds=[row['artifactId'] for row in record['subject']['media']],
            approvalId=record['id'], payloadHash=record['payloadHash'])
        started = await self.workflow.start(platform, payload, before_claim=verify_dispatch,
            on_result=lambda finished: self.store.publication_result(identifier, binding, finished))
        self.store.publication_result(identifier, binding, started)
        return self.store.get(identifier)

    def observe(self, identifier):
        project = self.store.get(identifier)
        actor = self.workflow.scope()['actor_id']
        for binding in project['publication_runs']:
            if binding['actor_id'] != actor:
                continue
            try:
                record = self.workflow.get(binding['approval_id'])
            except HTTPException as error:
                if error.status_code in {403, 404}:
                    continue
                raise
            self.store.publication_result(identifier, {key: value for key, value in binding.items()
                if key not in {'result', 'operation_id', 'state'}}, record)
        return self.store.get(identifier)
