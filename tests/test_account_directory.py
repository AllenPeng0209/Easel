"""One account directory must not turn platform slots into duplicate people."""
import importlib
import importlib.util
import json

import pytest


def directory():
    assert importlib.util.find_spec('easel.account_directory') is not None, 'A single account directory is missing'
    return importlib.import_module('easel.account_directory')


def platform(key, *, logged=False, backend='web', name=None):
    return {'platform': key, 'name': name or key, 'backend': backend, 'supported': True,
            'loggedIn': logged, 'note': ''}


def account(identifier, subject, *, platform='douyin', nickname='Same nickname'):
    return {'id': identifier, 'platform': platform, 'uid': subject if platform == 'douyin' else '',
            'sec_uid': 'secure-' + subject if platform == 'douyin' else subject,
            'nickname': nickname, 'avatar': '', 'status': 'active', 'has_auth': True}


def shared(identifier, subject, *, platform='douyin'):
    identity = {'accountId': subject, 'accountIdKind': 'uid' if platform == 'douyin' else 'user_id',
                'accountIdentityStatus': 'verified'}
    return {'account_id': identifier, 'identity': identity, 'storage_state': {'private': 'do-not-return'},
            'capabilities': {'creator': 'verified', 'publish': 'verified', 'analytics': 'verified',
                             'comments': 'verified', 'dm': 'needs_verification', 'storage_state': 'do-not-return'}}


def test_empty_directory_keeps_the_full_platform_catalog_without_unlogged_account_rows():
    module = directory()
    catalog = [platform('douyin', name='抖音'), platform('xiaohongshu', name='小红书'),
               platform('youtube', backend='social-browser', name='YouTube')]
    result = module.build_account_directory('own-ip', catalog)
    assert result['project_id'] == 'own-ip'
    assert result['platforms'] == catalog
    assert result['accounts'] == []


def test_one_shared_subject_is_one_row_and_the_old_content_slot_is_absorbed():
    module = directory()
    result = module.build_account_directory('default', [platform('douyin', logged=True)],
        [account(1, 'alice')], shared_sessions={'douyin': shared(1, 'alice')})
    assert len(result['accounts']) == 1
    row = result['accounts'][0]
    assert row['key'].startswith('native:1:') and row['nativeAccountId'] == 1
    assert row['current'] and row['identityVerified']
    assert row['capabilities']['publish'] == 'verified'
    assert row['capabilities']['dm'] == 'needs_verification'
    assert 'do-not-return' not in json.dumps(result)


def test_two_same_named_native_accounts_are_not_merged_and_only_one_is_current():
    module = directory()
    result = module.build_account_directory('default', [platform('douyin', logged=True)],
        [account(1, 'alice'), account(2, 'bob')], shared_sessions={'douyin': shared(1, 'alice')})
    rows = result['accounts']
    assert [row['nativeAccountId'] for row in rows] == [1, 2]
    assert rows[0]['key'] != rows[1]['key']
    assert [row['current'] for row in rows] == [True, False]
    assert rows[1]['capabilities']['publish'] != 'verified'


def test_snapshot_internal_id_alone_cannot_merge_conflicting_platform_subjects():
    module = directory()
    result = module.build_account_directory('default', [platform('douyin', logged=True)],
        [account(1, 'bob')], shared_sessions={'douyin': shared(1, 'alice')})
    assert len(result['accounts']) == 2
    native = next(row for row in result['accounts'] if row['key'].startswith('native:1:'))
    assert not native['current']
    assert native['capabilities']['creator'] != 'verified'
    assert native['capabilities']['publish'] != 'verified'


def test_legacy_only_xhs_stays_visible_without_inventing_native_identity_or_interaction_access():
    module = directory()
    result = module.build_account_directory('ip-a', [platform('xiaohongshu', logged=True, backend='xhs', name='小红书')])
    row, = result['accounts']
    assert row['key'] == 'legacy:xiaohongshu' and row['nativeAccountId'] is None
    assert row['loggedIn'] and not row['identityVerified']
    assert row['status'] == 'unknown'
    assert row['name'] == '小红书账号'
    assert row['capabilities']['comments'] != 'verified' and row['capabilities']['dm'] != 'verified'


def test_verified_whoami_merges_only_same_namespace_and_xhs_alias_is_normalized():
    module = directory()
    catalog = [platform('xiaohongshu', logged=True, backend='xhs', name='小红书')]
    whoami = {'xiaohongshu': {'loggedIn': True, 'name': 'Own XHS', 'avatar': '',
                             'accountId': 'user-1', 'accountIdKind': 'userId', 'accountIdentityStatus': 'verified'}}
    result = module.build_account_directory('ip-a', catalog, [account(2, 'user-1', platform='xhs')],
        legacy_identities=whoami)
    assert len(result['accounts']) == 1 and result['accounts'][0]['platform'] == 'xiaohongshu'
    wrong = {'douyin': {**whoami['xiaohongshu'], 'accountId': 'alice', 'accountIdKind': 'sec_uid'}}
    result = module.build_account_directory('ip-a', [platform('douyin', logged=True)],
        [account(1, 'alice')], legacy_identities=wrong)
    assert len(result['accounts']) == 2, 'uid and sec_uid are different namespaces even with equal text'


def test_session_only_platform_does_not_claim_publish_or_interaction_permissions():
    module = directory()
    result = module.build_account_directory('ip-a', [platform('youtube', logged=True, backend='social-browser')])
    assert set(result['accounts'][0]['capabilities'].values()) == {'unavailable'}


def test_partial_creator_login_is_not_erased_by_invalid_main_site_status():
    module = directory()
    native = {**account(1, 'alice'), 'status': 'invalid'}
    record = shared(1, 'alice')
    record['capabilities'].update(comments='needs_verification', dm='needs_verification')
    row, = module.build_account_directory('default', [platform('douyin', logged=True)],
        [native], shared_sessions={'douyin': record})['accounts']
    assert row['loggedIn'] and row['status'] == 'unknown'
    assert row['capabilities']['creator'] == 'verified'
    assert row['capabilities']['comments'] == 'needs_verification'


def test_verified_legacy_login_is_retained_when_matching_native_profile_needs_relogin():
    module = directory()
    native = {**account(1, 'alice'), 'status': 'invalid', 'has_auth': False}
    whoami = {'douyin': {'loggedIn': True, 'name': 'Alice', 'accountId': 'alice',
                          'accountIdKind': 'uid', 'accountIdentityStatus': 'verified'}}
    row, = module.build_account_directory('default', [platform('douyin', logged=True)],
        [native], legacy_identities=whoami)['accounts']
    assert row['loggedIn'] and row['status'] == 'unknown'
    assert row['capabilities']['creator'] != 'verified'


def test_missing_native_database_does_not_create_runtime_state(tmp_path):
    module = directory()
    assert module.read_native_accounts(tmp_path) == []
    assert not (tmp_path / '.easel').exists()


def test_corrupt_native_database_is_an_error_instead_of_an_empty_directory(tmp_path):
    module = directory()
    path = tmp_path / '.easel/operations/operations.sqlite3'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'private-fixture-is-not-a-database')
    with pytest.raises(module.AccountDirectoryError) as caught:
        module.read_native_accounts(tmp_path)
    assert 'private-fixture' not in str(caught.value)
