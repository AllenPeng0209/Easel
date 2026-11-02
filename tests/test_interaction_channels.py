import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

_ADAPTER_WORKSPACE: Path | None = None


@pytest.fixture(autouse=True)
def isolated_adapter_workspace(tmp_path, monkeypatch):
    # Protocol tests must not inherit the operator's real local users or tokens.
    monkeypatch.setitem(globals(), '_ADAPTER_WORKSPACE', tmp_path)


def module():
    path = Path(__file__).resolve().parents[1] / 'easel' / 'interaction_channels.py'
    assert path.exists(), 'Interaction channel adapters have not been implemented'
    spec = importlib.util.spec_from_file_location('interaction_channels_under_test', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    assert _ADAPTER_WORKSPACE is not None, 'Channel tests need an isolated workspace'
    value.ROOT = _ADAPTER_WORKSPACE
    return value


def channel(platform='douyin', kind='comment', mode='official'):
    return {'id': 'channel-one', 'client_id': 'client-one', 'platform': platform,
            'kind': kind, 'delivery_mode': mode, 'enabled': True,
            'source_url': '', 'config': {'env_prefix': 'EASEL_INTERACTION_TEST', 'video_id': 'video+one='}}


def environment():
    return {'EASEL_INTERACTION_TEST_ACCESS_TOKEN': 'test-token-not-a-real-secret',
            'EASEL_INTERACTION_TEST_OPEN_ID': 'open+one=',
            'EASEL_INTERACTION_TEST_PERMISSION_VERIFIED': 'true'}


def conversation():
    return {'id': 'conversation-one', 'last_inbound_external_id': 'comment+one=',
            'source_url': 'https://www.douyin.com/video/123'}


def test_pending_private_message_channels_do_not_claim_connected_or_send():
    adapter = module().InteractionChannels(environ={})
    for platform in ('douyin', 'xiaohongshu'):
        capability = adapter.capabilities(channel(platform, 'dm'))
        assert capability['can_sync'] is False and capability['can_send'] is False
        assert capability['state'] == 'awaiting_authorization'
        result = adapter.send(channel(platform, 'dm'), conversation(), {'text': '你好'}, 'send-one')
        assert result['status'] == 'failed'


def test_simulation_is_not_forwarded_to_a_real_channel_adapter():
    def remote(_request):
        pytest.fail('Simulation reached a real platform')
    adapter = module().InteractionChannels(environ=environment(), transport=httpx.MockTransport(remote))
    result = adapter.send(channel(mode='simulation'), conversation(), {'text': '你好'}, 'send-one')
    assert result['status'] == 'failed'
    assert result['external_message_id'] == ''


def test_missing_credentials_prevent_all_outbound_requests():
    def remote(_request):
        pytest.fail('Unconfigured channel reached the platform')
    adapter = module().InteractionChannels(environ={}, transport=httpx.MockTransport(remote))
    assert adapter.capabilities(channel())['state'] == 'awaiting_authorization'
    assert adapter.send(channel(), conversation(), {'text': '你好'}, 'send-one')['status'] == 'failed'


def test_arbitrary_environment_variable_prefix_cannot_supply_credentials():
    adapter = module().InteractionChannels(environ={'OPENAI_ACCESS_TOKEN': 'private'})
    current = channel(); current['config']['env_prefix'] = 'OPENAI'
    assert adapter.capabilities(current)['can_send'] is False


def test_credentials_alone_do_not_assert_the_account_has_permission():
    env = environment(); env.pop('EASEL_INTERACTION_TEST_PERMISSION_VERIFIED')
    adapter = module().InteractionChannels(environ=env)
    capability = adapter.capabilities(channel())
    assert capability['state'] == 'awaiting_authorization'
    assert capability['can_send'] is False


def test_official_comment_list_uses_fixed_host_and_normalizes_exact_ids():
    def remote(request):
        assert request.method == 'GET'
        assert request.url.host == 'open.douyin.com'
        assert request.url.path == '/video/comment/list/'
        assert request.url.params['open_id'] == 'open+one='
        assert request.url.params['item_id'] == 'video+one='
        assert request.headers['access-token'] == environment()['EASEL_INTERACTION_TEST_ACCESS_TOKEN']
        return httpx.Response(200, json={'data': {'error_code': 0, 'has_more': False,
            'list': [{'comment_id': 'comment+one=', 'comment_user_id': 'visitor-one', 'content': '课程怎么安排？'}]},
            'extra': {'error_code': 0}})
    adapter = module().InteractionChannels(environ=environment(), transport=httpx.MockTransport(remote))
    rows = adapter.sync(channel())
    assert rows == [{'external_id': 'comment+one=', 'text': '课程怎么安排？', 'author_name': 'visitor-one',
                     'conversation_external_id': 'comment+one=', 'source_url': '', 'subject': '抖音视频评论'}]


def test_comment_sync_deduplicates_bounded_pages():
    calls = []
    def remote(request):
        calls.append(request.url.params['cursor'])
        return httpx.Response(200, json={'data': {'error_code': 0, 'has_more': len(calls) == 1, 'cursor': 4,
            'list': [{'comment_id': 'comment-one', 'content': 'hello'}]}, 'extra': {'error_code': 0}})
    adapter = module().InteractionChannels(environ=environment(), transport=httpx.MockTransport(remote))
    assert len(adapter.sync(channel())) == 1
    assert calls == ['0', '4']


def test_platform_business_error_is_not_treated_as_an_empty_successful_import():
    def remote(_request):
        return httpx.Response(200, json={'data': {'error_code': 2190004, 'description': 'private upstream text'}})
    value = module()
    adapter = value.InteractionChannels(environ=environment(), transport=httpx.MockTransport(remote))
    with pytest.raises(value.ChannelError) as caught:
        adapter.sync(channel())
    assert 'private upstream text' not in str(caught.value)


def test_reply_uses_exact_inbound_id_and_requires_a_platform_message_receipt():
    calls = []
    def remote(request):
        calls.append(request)
        assert request.method == 'POST' and request.url.host == 'open.douyin.com'
        assert request.url.path == '/video/comment/reply/'
        assert request.url.params['open_id'] == 'open+one='
        assert json.loads(request.content) == {'item_id': 'video+one=', 'comment_id': 'comment+one=', 'content': '已收到咨询。'}
        return httpx.Response(200, json={'data': {'error_code': 0, 'comment_id': 'reply-one'}, 'extra': {'error_code': 0}})
    adapter = module().InteractionChannels(environ=environment(), transport=httpx.MockTransport(remote))
    result = adapter.send(channel(), conversation(), {'text': '已收到咨询。'}, 'operation-one')
    assert result == {'status': 'sent', 'external_message_id': 'reply-one', 'receipt_id': 'operation-one',
                      'message': '平台已返回回复编号', 'mode': 'official'}
    assert len(calls) == 1


def test_no_exact_target_id_does_not_fall_back_to_a_nickname_or_top_level_comment():
    def remote(_request):
        pytest.fail('Missing target ID reached the platform')
    adapter = module().InteractionChannels(environ=environment(), transport=httpx.MockTransport(remote))
    assert adapter.send(channel(), {'author_name': 'same nickname'}, {'text': '你好'}, 'send-one')['status'] == 'failed'


def test_reply_preserves_the_exact_approved_draft_text():
    approved_text = ' 第一段\n第二段 '
    def remote(request):
        assert json.loads(request.content)['content'] == approved_text
        return httpx.Response(200, json={'data': {'error_code': 0, 'comment_id': 'reply-one'}})
    adapter = module().InteractionChannels(environ=environment(), transport=httpx.MockTransport(remote))
    assert adapter.send(channel(), conversation(), {'text': approved_text}, 'send-one')['status'] == 'sent'


def test_timeout_preserves_unknown_submission_without_retry_or_secret_echo():
    calls = []
    def remote(request):
        calls.append(request)
        raise httpx.ReadTimeout('test-token-not-a-real-secret', request=request)
    adapter = module().InteractionChannels(environ=environment(), transport=httpx.MockTransport(remote))
    result = adapter.send(channel(), conversation(), {'text': '你好'}, 'operation-one')
    assert result['status'] == 'unknown' and result['external_message_id'] == ''
    assert len(calls) == 1
    assert 'test-token-not-a-real-secret' not in json.dumps(result)


@pytest.mark.parametrize('status,payload', [
    (200, {'data': {'error_code': 0}, 'extra': {'error_code': 0}}),
    (502, {'detail': 'private upstream body'}),
    (307, {'detail': 'redirect'}),
])
def test_ambiguous_response_never_claims_delivery(status, payload):
    calls = []
    def remote(request):
        calls.append(request)
        return httpx.Response(status, json=payload, headers={'Location': 'https://untrusted.example/'})
    adapter = module().InteractionChannels(environ=environment(), transport=httpx.MockTransport(remote))
    result = adapter.send(channel(), conversation(), {'text': '你好'}, 'send-one')
    assert result['status'] == 'unknown'
    assert len(calls) == 1
    assert 'private upstream body' not in json.dumps(result)


def test_explicit_platform_rejection_is_failed_without_exposing_body():
    def remote(_request):
        return httpx.Response(200, json={'data': {'error_code': 2190004, 'description': 'private-account-value'}})
    adapter = module().InteractionChannels(environ=environment(), transport=httpx.MockTransport(remote))
    result = adapter.send(channel(), conversation(), {'text': '你好'}, 'send-one')
    assert result['status'] == 'failed' and 'private-account-value' not in json.dumps(result)


def test_xhs_comment_sending_stays_manual_even_when_browser_profile_exists(tmp_path):
    profile = tmp_path / '.scratch/interaction_profiles/default/client-one/channel-one/XiaohongshuProfile'; profile.mkdir(parents=True)
    def runner(*_args, **_kwargs):
        pytest.fail('Unverified nickname-based send was executed')
    adapter = module().InteractionChannels(environ={}, workspace=tmp_path, runner=runner)
    current = channel('xiaohongshu', mode='manual')
    assert adapter.capabilities(current)['can_send'] is False
    assert adapter.send(current, conversation(), {'text': '你好'}, 'send-one')['status'] == 'failed'


def test_identical_client_ids_cannot_reuse_browser_login_from_another_project(tmp_path):
    from easel.projects import project_context
    profile = tmp_path / '.scratch/interaction_profiles/space-a/client-one/channel-one/XiaohongshuProfile'
    profile.mkdir(parents=True)
    adapter = module().InteractionChannels(workspace=tmp_path, environ={})
    current = channel('xiaohongshu', mode='manual')
    with project_context('space-a'):
        assert adapter.capabilities(current)['can_sync'] is True
    with project_context('space-b'):
        assert adapter.capabilities(current)['can_sync'] is False


def test_different_channels_of_one_client_do_not_share_browser_accounts(tmp_path):
    profile = tmp_path / '.scratch/interaction_profiles/default/client-one/channel-one/XiaohongshuProfile'
    profile.mkdir(parents=True)
    adapter = module().InteractionChannels(workspace=tmp_path, environ={})
    first = channel('xiaohongshu', mode='manual')
    second = {**first, 'id': 'channel-two'}
    assert adapter.capabilities(first)['can_sync'] is True
    assert adapter.capabilities(second)['can_sync'] is False


def test_official_xhs_channel_never_silently_uses_the_manual_browser_profile(tmp_path):
    profile = tmp_path / '.scratch/interaction_profiles/default/client-one/channel-one/XiaohongshuProfile'; profile.mkdir(parents=True)
    def runner(*_args, **_kwargs):
        pytest.fail('Official channel silently fell back to browser automation')
    value = module(); adapter = value.InteractionChannels(workspace=tmp_path, runner=runner, environ={})
    current = channel('xiaohongshu', mode='official')
    assert adapter.capabilities(current)['can_sync'] is False
    with pytest.raises(value.ChannelError):
        adapter.sync(current, 'https://www.xiaohongshu.com/explore/0123456789abcdef01234567')


def test_xhs_import_rejects_untrusted_urls_before_launching_a_browser(tmp_path):
    def runner(*_args, **_kwargs):
        pytest.fail('Untrusted URL reached the browser')
    value = module(); adapter = value.InteractionChannels(workspace=tmp_path, runner=runner, environ={})
    with pytest.raises(value.ChannelError):
        adapter.sync(channel('xiaohongshu', mode='manual'), 'https://xiaohongshu.com.evil.example/explore/abc')


def test_xhs_import_uses_scoped_profile_argv_and_private_output(tmp_path):
    profile = tmp_path / '.scratch/interaction_profiles/default/client-one/channel-one/XiaohongshuProfile'; profile.mkdir(parents=True)
    source_url = 'https://www.xiaohongshu.com/explore/0123456789abcdef01234567?xsec_token=sample-private-access'
    def runner(argv, **kwargs):
        assert all('sample-private-access' not in argument for argument in argv)
        assert isinstance(argv, list) and argv[1] == '-c' and '--exec' not in argv
        assert kwargs.get('shell', False) is False
        payload = json.loads(kwargs['input'])
        assert payload['source_url'] == source_url
        assert payload['profile_base'] == str(profile.parent)
        output = Path(payload['output'])
        assert os.stat(output).st_mode & 0o777 == 0o600
        assert os.stat(output.parent).st_mode & 0o777 == 0o700
        output.write_text(json.dumps({'comments': [{'id': 'comment-one', 'nickname': '模拟访客', 'content': '你好', 'parent': ''}]}))
        return SimpleNamespace(returncode=0, stdout='ignored', stderr='')
    adapter = module().InteractionChannels(workspace=tmp_path, runner=runner, environ={})
    rows = adapter.sync(channel('xiaohongshu', mode='manual'), source_url)
    assert rows[0]['external_id'] == 'comment-one'
    assert rows[0]['source_url'] == 'https://www.xiaohongshu.com/explore/0123456789abcdef01234567'
    assert 'sample-private-access' not in json.dumps(rows)


def test_configured_channel_token_cannot_be_sent_as_reply_text():
    def remote(_request):
        pytest.fail('The channel credential was copied into an outbound reply')
    env = environment()
    adapter = module().InteractionChannels(environ=env, transport=httpx.MockTransport(remote))
    result = adapter.send(channel(), conversation(), {'text': env['EASEL_INTERACTION_TEST_ACCESS_TOKEN']}, 'send-one')
    assert result['status'] == 'failed'
    assert env['EASEL_INTERACTION_TEST_ACCESS_TOKEN'] not in json.dumps(result)


def test_xhs_subprocess_failure_does_not_expose_profile_or_token(tmp_path):
    profile = tmp_path / '.scratch/interaction_profiles/default/client-one/channel-one/XiaohongshuProfile'; profile.mkdir(parents=True)
    def runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout='private-cookie', stderr='private-token')
    value = module(); adapter = value.InteractionChannels(workspace=tmp_path, runner=runner, environ={})
    with pytest.raises(value.ChannelError) as caught:
        adapter.sync(channel('xiaohongshu', mode='manual'), 'https://www.xiaohongshu.com/explore/0123456789abcdef01234567')
    assert 'private' not in str(caught.value)
