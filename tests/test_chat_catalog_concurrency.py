"""C19: catalog readers and competing admission must not renegotiate journaling."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
import threading

import pytest

from web.chat_history import ChatHistoryStore


SAVED_SESSION = {
    'id': 'alpha', 'title': '已有对话', 'created': 1, 'updated': 2,
    'messages': [{'role': 'user', 'content': '保留已有内容'}],
}


def existing_catalog(directory, journal_mode):
    """An independently seeded, complete valid catalog, not a production builder."""
    directory.mkdir()
    path = directory / 'chat_catalog.sqlite3'
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(f'PRAGMA journal_mode={journal_mode}').fetchone()[0].lower() == journal_mode.lower()
        connection.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, updated REAL NOT NULL, '
                           'payload TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)')
        connection.execute('CREATE TABLE turn_claims (turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, '
                           'payload_hash TEXT NOT NULL, owner_pid INTEGER NOT NULL)')
        connection.execute('INSERT INTO sessions VALUES(?,?,?,0)',
                           ('alpha', 2, json.dumps(SAVED_SESSION, ensure_ascii=False)))
        connection.commit()
    return path


def test_existing_delete_catalog_can_be_constructed_and_read_while_reader_holds_snapshot(tmp_path):
    directory = tmp_path / 'private-user-ip'
    path = existing_catalog(directory, 'delete')
    with closing(sqlite3.connect(path)) as reader:
        reader.execute('BEGIN')
        assert reader.execute('SELECT id FROM sessions').fetchall() == [('alpha',)]
        # This real read transaction stays open throughout construction and read.
        # Mode conversion needs a lock upgrade; ordinary catalog reads do not.
        store = ChatHistoryStore(directory)
        assert store.sessions() == [SAVED_SESSION]
        assert reader.in_transaction
        with closing(sqlite3.connect(path)) as observer:
            assert observer.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        reader.rollback()


def test_existing_wal_catalog_is_not_downgraded_by_reads_or_writes(tmp_path):
    directory = tmp_path / 'existing-wal'
    path = existing_catalog(directory, 'wal')
    with closing(sqlite3.connect(path)) as reader:
        reader.execute('BEGIN')
        assert reader.execute('SELECT id FROM sessions').fetchall() == [('alpha',)]
        store = ChatHistoryStore(directory)
        assert store.sessions() == [SAVED_SESSION]
        second = {'id': 'beta', 'title': '新对话', 'created': 3, 'updated': 3, 'messages': []}
        store.merge([second])
        assert [session['id'] for session in store.sessions()] == ['beta', 'alpha']
        # The already open reader keeps its own valid acknowledged prefix.
        assert reader.execute('SELECT id FROM sessions ORDER BY id').fetchall() == [('alpha',)]
        reader.rollback()
    with closing(sqlite3.connect(path)) as observer:
        assert observer.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert observer.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('workers', [2, 8])
def test_fresh_concurrent_claims_have_one_winner_and_keep_replay_prefix(tmp_path, workers):
    directory = tmp_path / f'fresh-{workers}'
    event_path = directory / 'jobs' / 'one.jsonl'
    barrier = threading.Barrier(workers, timeout=10)
    def claim(_):
        barrier.wait()
        return ChatHistoryStore(directory).claim_turn('one', 'session', 'payload-hash', event_path)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(claim, range(workers)))
    assert sum(won for won, _ in results) == 1
    assert all(record['sessionId'] == 'session' and record['payloadHash'] == 'payload-hash'
               for _, record in results)
    event_path.write_bytes(b'accepted-event-prefix\n')
    won, original = ChatHistoryStore(directory).claim_turn('one', 'session', 'payload-hash', event_path)
    assert won is False
    assert original['sessionId'] == 'session'
    assert event_path.read_bytes() == b'accepted-event-prefix\n'
    with closing(sqlite3.connect(directory / 'chat_catalog.sqlite3')) as connection:
        assert connection.execute('SELECT COUNT(*) FROM turn_claims').fetchone()[0] == 1
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
