"""Current-IP authenticated bridge for native AI video tool callbacks."""
from pathlib import Path

from easel.video_provider import NativeVideoClient
from easel.host_state import generation_state_directory
from easel.video_generation import VideoGenerationService, video_generation_payload


def _service(*, repository: Path, workspace: Path, outputs: Path,
             actor_id: str, ip: str, session_id: str, authorize):
    authorize()
    workspace = Path(workspace).resolve()
    outputs = Path(outputs)
    if outputs.is_symlink() or outputs.resolve() != workspace / "outputs":
        raise ValueError("视频制作只能使用当前 IP 的内容目录")
    return VideoGenerationService(outputs=outputs, client=NativeVideoClient(repository=repository),
        state_directory=generation_state_directory(repository, ip),
        actor_id=actor_id, ip=ip, session_id=session_id, authorize=authorize, repository=repository)


def run_video_generation(payload: object, **scope) -> dict:
    scope['authorize']()
    payload = video_generation_payload(payload)
    return _service(**scope).run(payload)


async def approve_video_production_plan(payload: object, *, ask, **scope) -> dict:
    """The model requests a question; it never supplies a positive decision."""
    scope['authorize']()
    payload = video_generation_payload(payload)
    if payload['action'] != 'approve_plan':
        raise ValueError('本入口只接收当前制作计划的真人批准请求')
    service = _service(**scope)
    plan = service.run({**payload, 'action': 'plan_status'})
    if plan['state'] != 'pending':
        return plan
    references = [reference for clip in plan['clips'] for reference in clip['references']]
    reference_count = len({(item['path'], item['sha256']) for item in references})
    supersession = plan.get('supersession')
    replacement_notice = ''
    if supersession:
        replacement_notice = (
            f"本次被替换的原计划已提交 {len(supersession['collectedClaims'])} 个片段，原片及回执继续保留。"
            f"批准后关闭原计划未提交的 {len(supersession['unsubmittedClipIds'])} 个片段。"
            f"本次最多新增 {plan['maxGenerations']} 次固定生成请求，不复用或释放原提交额度。")
    question = {'questionId': 'video-production', 'kind': 'approval', 'requiresHuman': True,
        'header': '视频制作确认',
        'question': (f"是否批准制作《{plan['title']}》？模型：{plan['modelId'].split('::')[-1]}；"
            f"{plan['aspectRatio']}，目标{plan['resolution']}。本方案最多新生成 {plan['maxGenerations']} 个固定片段，"
            f"请求总时长 {plan['totalSeconds']} 秒；每片仅提交一次，未列出的返修不在本卡范围内。"
            f"使用 {reference_count} 个本 IP 参考文件（片段引用共 {len(references)} 次），已核验实际媒体。"
            "本卡确认生成范围与提交额度。目录未登记计价信息；已授权的自部署算力按原授权使用。"
            "新增外部收费或资源需另行确认。"
            f"{replacement_notice}"
            f"制作范围摘要：{plan['scopeHash'][:16]}。"),
        'options': [{'label': '批准此方案生成', 'description': '仅允许这份固定计划的明确片段额度。'},
                    {'label': '暂不生成', 'description': '保留计划，不发起模型请求。'}]}
    answers = await ask(question)
    scope['authorize']()
    fresh = service.run({**payload, 'action': 'plan_status'})
    if fresh['scopeHash'] != plan['scopeHash'] or fresh['state'] != 'pending':
        if fresh['scopeHash'] == plan['scopeHash'] and fresh['state'] == 'approved':
            return fresh
        raise ValueError('制作计划已变化或批准已过期，未开始生成')
    return service.plan_decision(payload['workshopId'], plan['planId'], plan['scopeHash'],
        isinstance(answers, dict) and answers.get('video-production') == ['批准此方案生成'])
