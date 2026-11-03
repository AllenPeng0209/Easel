"""D06 round observations cover real files, net content, and bounded evidence."""
import os
import io

import pytest
from PIL import Image

from tests.test_agent_harnesses import sandbox
from tests.test_chat_artifacts import artifact_http_environment


def test_public_outputs_net_changes_include_text_and_shell_only_binary(tmp_path):
    from web.output_turn_changes import OutputTurnRecorder

    outputs = tmp_path / 'outputs'
    media = outputs / 'campaign'
    media.mkdir(parents=True)
    (media / 'draft.txt').write_text('keep\nold\n')
    (media / 'notes.txt').write_text('first\n')
    (media / 'unchanged.txt').write_text('same\n')
    (outputs / '_login').mkdir()
    (outputs / '_login' / 'private.txt').write_text('old private credential')
    (media / 'credentials.txt').write_text('old forbidden credential')
    outside = tmp_path / 'foreign'
    outside.mkdir()
    (outside / 'foreign.txt').write_text('old foreign')
    (media / 'linked').symlink_to(outside, target_is_directory=True)
    os.link(outside / 'foreign.txt', media / 'hardlink.txt')

    recorder = OutputTurnRecorder(outputs)
    recorder.begin()
    (media / 'draft.txt').write_text('keep\nnew\n')
    (media / 'notes.txt').write_text('first\nsecond\n')
    Image.new('RGB', (2, 2), 'green').save(media / 'cover.png')
    (media / 'unchanged.txt').write_text('temporary\n')
    (media / 'unchanged.txt').write_text('same\n')
    (outputs / '_login' / 'private.txt').write_text('new private credential')
    (media / 'credentials.txt').write_text('new forbidden credential')
    (outside / 'foreign.txt').write_text('new foreign')
    summary = recorder.finish()

    assert summary['total'] == 3 and summary['coverage'] == 'complete'
    assert summary['warnings'] == ['shared_workspace']
    assert summary['files'] == [
        {'path': 'campaign/cover.png', 'kind': 'added', 'diffStatus': 'binary'},
        {'path': 'campaign/draft.txt', 'kind': 'modified', 'diffStatus': 'text', 'added': 1, 'deleted': 1},
        {'path': 'campaign/notes.txt', 'kind': 'modified', 'diffStatus': 'text', 'added': 1, 'deleted': 0},
    ]


def test_oversized_comparisons_have_no_guessed_counts_and_noop_is_absent(tmp_path, monkeypatch):
    from web import output_turn_changes as changes

    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    path = outputs / 'long.txt'
    path.write_text('a' * 40)
    unchanged = changes.OutputTurnRecorder(outputs)
    unchanged.begin()
    assert unchanged.finish() is None

    monkeypatch.setattr(changes, '_MAX_TEXT_BYTES', 8)
    observed = changes.OutputTurnRecorder(outputs)
    observed.begin()
    path.write_text('b' * 40)
    assert observed.finish()['files'] == [
        {'path': 'long.txt', 'kind': 'modified', 'diffStatus': 'oversized'},
    ]


def test_overlap_marks_both_observations_without_attributing_writer(tmp_path):
    from web.output_turn_changes import OutputTurnRecorder

    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    first, second = OutputTurnRecorder(outputs), OutputTurnRecorder(outputs)
    first.begin()
    second.begin()
    (outputs / 'shared.txt').write_text('shared\n')
    for recorder in (first, second):
        summary = recorder.finish()
        assert summary['coverage'] == 'partial'
        assert 'concurrent_observation' in summary['warnings']
        assert summary['files'][0]['path'] == 'shared.txt'


def test_scan_limit_marks_unknown_without_inventing_additions(tmp_path, monkeypatch):
    from web import output_turn_changes as changes

    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    (outputs / 'initial.txt').write_text('existing')
    (outputs / 'another.txt').write_text('existing')
    monkeypatch.setattr(changes, '_MAX_ENTRIES', 1)
    recorder = changes.OutputTurnRecorder(outputs)
    recorder.begin()
    (outputs / 'new.txt').write_text('could not verify baseline')
    summary = recorder.finish()
    assert summary['coverage'] == 'partial' and 'scan_limit' in summary['warnings']
    assert summary['files'] == []


def test_replace_eligible_tree_with_symlink_is_a_coverage_gap_not_deletion(tmp_path):
    from web.output_turn_changes import OutputTurnRecorder

    outputs = tmp_path / 'outputs'
    public = outputs / 'project'
    public.mkdir(parents=True)
    (public / 'file.txt').write_text('public')
    outside = tmp_path / 'external'
    outside.mkdir()
    (outside / 'file.txt').write_text('outside')
    recorder = OutputTurnRecorder(outputs)
    recorder.begin()
    public.rename(tmp_path / 'moved')
    public.symlink_to(outside, target_is_directory=True)
    summary = recorder.finish()
    assert summary['files'] == [] and summary['coverage'] == 'partial'
    assert 'unreadable' in summary['warnings']


def _change_events(text):
    import json
    return [json.loads(line[6:]) for line in text.splitlines()
            if line.startswith('data: {') and '"kind": "changes"' in line]


def test_turn_summary_survives_live_replay_and_cold_snapshot(sandbox, monkeypatch):
    import web.app as web

    outputs = web.OUTPUTS_DIR
    outputs.mkdir(exist_ok=True)
    file = outputs / 'script.txt'
    file.write_text('before\n')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'workspace-write'})

    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('output-change-fixture')
            file.write_text('after\n')
            emit('token', '已完成')

    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    response = sandbox.post('/api/chat/stream', json={'message': 'update script', 'sessionId': 'changes-chat',
                                                     'turnId': 'changes-turn'})
    assert response.status_code == 200, response.text
    [event] = _change_events(response.text)
    assert event['source'] == 'outputs_snapshot' and event['status'] == 'completed'
    assert event['turnId'] == 'changes-turn'
    assert event['changes']['files'] == [{'path': 'script.txt', 'kind': 'modified', 'diffStatus': 'text',
                                          'added': 1, 'deleted': 1}]
    file.write_text('later edit\n')
    assert _change_events(sandbox.get('/api/chat/jobs/changes-turn/stream').text) == [event]
    assert sandbox.get('/api/chat/last/changes-chat').json()['events'] == [event]


def test_stopped_native_turn_records_known_partial_output_effects(sandbox, monkeypatch):
    import asyncio
    import json
    import web.app as web

    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'workspace-write'})
    web.OUTPUTS_DIR.mkdir(exist_ok=True)

    async def exercise():
        started = asyncio.Event()

        class Adapter:
            async def run(self, request, emit, ask, bind):
                bind('stopped-output-fixture')
                (web.OUTPUTS_DIR / 'partial.txt').write_text('written before stop\n')
                started.set()
                await asyncio.Future()

        monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
        response = await web.api_chat_stream(web.ChatRequest(message='generate', sessionId='stopped-changes',
                                                             turnId='stopped-changes-turn'))
        await asyncio.wait_for(started.wait(), 2)
        receipt = await web.api_chat_stop(web.StopRequest(sessionId='stopped-changes',
                                                        expectedTurnId='stopped-changes-turn'))
        assert receipt['stopped']
        frames = [frame async for frame in response.body_iterator]
        [event] = [json.loads(frame['data']) for frame in frames if frame['event'] == 'agent_event'
                   and '"kind": "changes"' in frame['data']]
        assert event['status'] == 'stopped' and event['changes']['files'][0]['path'] == 'partial.txt'
        assert (await web.api_chat_last('stopped-changes'))['events'] == [event]

    asyncio.run(exercise())


@pytest.mark.parametrize('embedded', [True, False])
@pytest.mark.access_integration
def test_only_embedded_openclaw_cli_observes_local_scope(artifact_http_environment, monkeypatch, embedded):
    import web.app as web

    _, owner, _, _, _ = artifact_http_environment
    ip = owner.post('/api/projects', json={'name': 'Embedded changes'}).json()['id'] if embedded else 'default'
    monkeypatch.setattr(web, '_chat_backend', lambda _: 'openclaw')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'openclaw', 'models': {}, 'sandbox': 'read-only'})
    monkeypatch.setattr(web, '_chat_message', lambda request: request.message)
    monkeypatch.setattr(web, '_resolve_transport', lambda _: 'cli')
    monkeypatch.setattr(web, '_heal_openclaw_session', lambda _: None)
    monkeypatch.setattr(web, '_agent_thinking_level', lambda: 'off')
    monkeypatch.setattr(web, 'GatewayClient', None)
    monkeypatch.setattr(web, 'question_bridge_supported', lambda: False)
    monkeypatch.setattr(web, 'SHARED_RAW_STREAM', web.PROJECT_ROOT / 'absent.jsonl')
    monkeypatch.setattr(web, 'DEBUG_DIR', web.PROJECT_ROOT / 'debug')
    monkeypatch.setattr(web, '_project_agent_command', lambda command, env: (
        [*command, '--local'] if embedded else command, env,
    ))

    class Process:
        def __init__(self, *args, **kwargs):
            assert web.project_id() == ip
            outputs = web._outputs_dir()
            outputs.mkdir(parents=True, exist_ok=True)
            (outputs / 'local.txt').write_text('generated\n')
            if embedded:
                (web.OUTPUTS_DIR / 'outside.txt').write_text('different IP\n')
            self.stdout = io.StringIO('answer\n')

        def poll(self):
            return 0

    monkeypatch.setattr(web.subprocess, 'Popen', Process)
    route = f'/api/projects/{ip}/chat/stream' if embedded else '/api/chat/stream'
    response = owner.post(route, json={'message': 'write local file' if embedded else '--local',
                                      'sessionId': 'cli-changes', 'turnId': 'cli-changes-turn'})
    assert response.status_code == 200, response.text
    events = _change_events(response.text)
    if embedded:
        assert len(events) == 1, response.text
        [event] = events
        assert event['changes']['files'] == [{'path': 'local.txt', 'kind': 'added', 'diffStatus': 'text',
                                              'added': 1, 'deleted': 0}]
    else:
        assert events == []
