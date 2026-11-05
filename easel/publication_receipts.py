"""D30 versioned publication observations; no transport or publisher execution.

The readback payload retains existing platform_readback.ReadbackResult.as_dict()
/ WorkItem fields. Legacy outcome='verified' means a candidate was found, not
public visibility. Writers must supply a real provider-response ACK and an
independent authenticated observation; never manufacture those from log text.
"""
from __future__ import annotations

import re


class PublicationReceiptError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


_CORRELATION = ('operationId', 'payloadHash', 'platform', 'mode')
_PHASES = {'not_started', 'submit_attempted', 'platform_ack', 'readback'}
_MODES = {'publish_now', 'create_draft', 'manual_export', 'scheduled'}
_NOT_STARTED = {'dry_run', 'help', 'validation_failed', 'runner_unavailable'}
_PUBLIC = {'published', 'public', '已发布', '开放浏览', 'publicly_visible'}
_NONPUBLIC = {'processing', 'reviewing', 'pending', 'private', 'scheduled', 'unknown', 'rejected', 'deleted', 'prohibited'}


def _host(value):
    if (not isinstance(value, dict) or not isinstance(value.get('operationId'), str)
            or not re.fullmatch(r'[a-f0-9]{32}', value['operationId'])
            or not isinstance(value.get('payloadHash'), str) or not re.fullmatch(r'[a-f0-9]{64}', value['payloadHash'])
            or not isinstance(value.get('platform'), str) or not value['platform']
            or not isinstance(value.get('mode'), str) or value['mode'] not in _MODES
            or not isinstance(value.get('approvedAccountId'), str) or not value['approvedAccountId']
            or any(ord(char) < 32 for char in value['approvedAccountId'])):
        raise PublicationReceiptError('Host 发布回执上下文无效')
    return {key: value[key] for key in _CORRELATION} | {'accountId': value['approvedAccountId']}


def _id(value):
    if type(value) is int:
        value = str(value) if value > 0 else ''
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+=-]{0,255}', value):
        return None
    if value.isdecimal() and int(value) == 0:
        return None
    return value


def _same_account(value, expected):
    return (isinstance(value, dict) and value.get('authenticated') is True
            and isinstance(value.get('id'), str) and value['id'] == expected)


def _time(value):
    return type(value) is int and value > 0


def _readback_evidence(readback, expected_account, expected_id, *, platform=None):
    """Match concrete ACK ID, never a title prefix or an arbitrary public URL."""
    if not isinstance(readback, dict) or readback.get('outcome') != 'verified':
        return False, 'readback_unverified', None
    matched, evidence = readback.get('matched'), readback.get('evidence')
    if not isinstance(matched, dict) or not isinstance(evidence, dict):
        return False, 'readback_missing', None
    if platform == 'xiaohongshu' and (
            any(type(evidence.get(key)) is not int or not 0 < evidence[key] <= 2**53 - 1
                for key in ('xhsSessionGeneration', 'xhsNativeAccountId'))
            or not isinstance(evidence.get('xhsSessionHash'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', evidence['xhsSessionHash'])):
        return False, 'readback_account_snapshot_unverified', None
    if _id(matched.get('platform_content_id')) != expected_id:
        return False, 'readback_id_mismatch', None
    if not _same_account(evidence.get('account'), expected_account):
        return False, 'readback_account_unverified', None
    if (evidence.get('method') != 'authenticated_readback' or evidence.get('matchMethod') != 'ack_id'
            or evidence.get('unique') is not True):
        return False, 'readback_ambiguous', None
    candidates = readback.get('candidates')
    if isinstance(candidates, list) and sum(isinstance(item, dict) and _id(item.get('platform_content_id')) == expected_id
                                            for item in candidates) > 1:
        return False, 'readback_ambiguous', None
    if evidence.get('statusSource') != 'explicit':
        return False, 'readback_status_unknown', None
    status = matched.get('status')
    if not isinstance(status, str) or not status.strip():
        return False, 'readback_status_unknown', None
    status = status.strip().lower()
    # A rejected/reviewing/private item has not necessarily ever been public,
    # so its absence of published_at_ms cannot erase an explicit, same-ID state.
    # Public confirmation alone requires the temporal/baseline proof below.
    if status in _NONPUBLIC:
        return False, 'provider_rejected' if status in {'rejected', 'prohibited'} else 'publication_not_public', status
    since, checked, published = evidence.get('sinceMs'), evidence.get('checkedAtMs'), matched.get('published_at_ms')
    if (evidence.get('baselineCaptured') is not True or not all(_time(value) for value in (since, checked, published))
            or checked < since or published < since - 1000 or published > checked + 1000):
        return False, 'readback_time_unverified', None
    if status in _PUBLIC and evidence.get('visibility') == 'public':
        return True, 'public_readback_confirmed', 'public'
    return False, 'readback_visibility_unknown', None


def normalize_receipt(host, observation=None, *, returncode=None, process_error=None, not_started_reason=None):
    """Return a whitelisted result from a correlated machine observation.

    Plain stdout, URLs, exit zero, generic success flags and old readback objects
    are deliberately unsupported evidence. Unknown results never authorize retry.
    """
    context = _host(host)
    result = {**context, 'status': 'unknown', 'phase': 'unknown', 'reason': 'machine_receipt_missing'}
    if type(returncode) is int:
        result['runnerExitCode'] = returncode
    machine = isinstance(observation, dict) and observation.get('kind') == 'publication_receipt'
    if machine:
        if type(observation.get('schemaVersion')) is not int or observation['schemaVersion'] != 1:
            raise PublicationReceiptError('不支持的发布回执版本', 409)
        if any(observation.get(key) != context[key] for key in _CORRELATION):
            raise PublicationReceiptError('发布回执与当前操作、对象或平台不匹配', 409)
        phase = observation.get('phase')
        result['phase'] = phase if isinstance(phase, str) and phase in _PHASES else 'unknown'
    if isinstance(not_started_reason, str) and not_started_reason in _NOT_STARTED:
        if machine and result['phase'] in {'submit_attempted', 'platform_ack', 'readback'}:
            raise PublicationReceiptError('回执执行阶段与 Host 未执行标记冲突', 409)
        return {**result, 'status': 'failed', 'reason': not_started_reason, 'notStarted': True}
    if not machine:
        return result
    if (observation.get('submissionAmbiguous') is True or context['platform'] == 'xiaohongshu'
            and 'submissionAmbiguous' in observation and type(observation['submissionAmbiguous']) is not bool):
        return {**result, 'status': 'unknown', 'reason': 'ambiguous_submission'}
    phase = result['phase']
    reason = observation.get('reason')
    if phase == 'not_started' and isinstance(reason, str) and reason in _NOT_STARTED:
        return {**result, 'status': 'failed', 'reason': reason, 'notStarted': True}
    if phase not in {'platform_ack', 'readback'}:
        reason = ('timeout_after_submission' if process_error == 'timeout' and phase == 'submit_attempted'
                  else 'interrupted_after_submission' if process_error == 'cancelled' and phase == 'submit_attempted'
                  else 'submission_not_acknowledged')
        return {**result, 'reason': reason}
    ack = observation.get('ack')
    if not isinstance(ack, dict) or ack.get('accepted') is not True or ack.get('source') != 'provider_response':
        return {**result, 'reason': 'provider_ack_missing'}
    identifier = _id(ack.get('id'))
    if identifier is None:
        return {**result, 'reason': 'provider_id_missing'}
    account = observation.get('account')
    if ack.get('kind') == 'draft' and context['platform'] == 'wechat-oa' and context['mode'] == 'create_draft':
        # MP's create endpoint returns a concrete draft ID, but need not repeat
        # the account probe performed by the Host under the profile lock. This
        # proves only draft creation; it is never a public-post confirmation.
        draft = {**result, 'externalId': identifier, 'publicationState': 'draft',
                 'evidenceMethod': 'provider_response'}
        if (isinstance(account, dict) and account.get('id') not in (None, '')
                and account['id'] != context['accountId']):
            return {**draft, 'reason': 'account_mismatch', 'identityVerification': 'account_mismatch'}
        identity = 'authenticated_submission' if _same_account(account, context['accountId']) else 'host_dispatch_only'
        return {**draft, 'status': 'draft_created', 'reason': 'provider_draft_created',
                'identityVerification': identity}
    if not _same_account(account, context['accountId']):
        return {**result, 'reason': 'submission_account_unverified'}
    if ack.get('kind') != 'post' or context['mode'] not in {'publish_now', 'scheduled'}:
        return {**result, 'reason': 'unsupported_provider_effect'}
    result.update(status='submitted', reason='provider_submission_acknowledged', externalId=identifier,
                  evidenceMethod='provider_response', publicationState='unknown')
    state = ack.get('state')
    if isinstance(state, str) and state in _NONPUBLIC:
        result['publicationState'] = state
    if context['mode'] == 'scheduled' and state == 'scheduled':
        result['status'] = 'scheduled'
    if phase == 'readback':
        confirmed, reason, publication_state = _readback_evidence(observation.get('readback'), context['accountId'], identifier,
                                                                platform=context['platform'])
        result['reason'] = reason
        if publication_state is not None:
            result['publicationState'] = publication_state
        if confirmed:
            result.update(status='confirmed', evidenceMethod='authenticated_readback')
        elif reason == 'provider_rejected':
            result.update(status='failed', evidenceMethod='authenticated_readback', notStarted=False)
    return result
