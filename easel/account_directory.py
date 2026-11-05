"""A safe, project-owned view over saved accounts and compatibility sessions.

Platform catalog entries describe available connectors, not saved people. A
content session is absorbed into a native row only with matching platform IDs.
This module never opens a browser or returns authentication material.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3


class AccountDirectoryError(RuntimeError):
    """Account metadata could not be read without hiding an existing account."""


CAPABILITIES = ('creator', 'comments', 'dm', 'publish', 'analytics')
_STATES = {'verified', 'needs_verification', 'unavailable', 'unknown'}
_MANAGED = {'douyin', 'xiaohongshu'}
_ALIASES = {'xhs': 'xiaohongshu', 'shipinhao': 'weixin-channels'}


def platform_key(value):
    return _ALIASES.get(value, value)


def _identifier(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ''
    value = str(value).strip()
    return value if value and value != '0' and len(value) <= 256 and all(ord(char) > 32 for char in value) else ''


def _namespace(platform, kind):
    return 'user_id' if platform == 'xiaohongshu' and kind in {'userId', 'user_id', 'sec_uid'} else kind


def subjects(platform, value, *, require_verified=False):
    if not isinstance(value, dict) or require_verified and value.get('accountIdentityStatus') != 'verified':
        return {}
    result = {}
    names = ('user_id', 'userId', 'sec_uid') if platform == 'xiaohongshu' else ('uid', 'sec_uid', 'open_id')
    for name in names:
        identifier = _identifier(value.get(name))
        if identifier:
            namespace = _namespace(platform, name)
            if namespace in result and result[namespace] != identifier:
                return {}
            result[namespace] = identifier
    identifier = _identifier(value.get('accountId'))
    kind = value.get('accountIdKind')
    if identifier and isinstance(kind, str):
        namespace = _namespace(platform, kind)
        if namespace in result and result[namespace] != identifier:
            return {}
        result[namespace] = identifier
    return result


def matching_subjects(first, second):
    common = first.keys() & second.keys()
    return bool(common) and all(first[key] == second[key] for key in common)


def account_key(kind, identifier, platform, identity):
    """Opaque action identity; a replaced slot must invalidate an old click."""
    prefix = f'{kind}:{identifier}'
    if not identity:
        return prefix
    digest = hashlib.sha256(json.dumps({'platform': platform, 'subjects': identity},
                                      sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:20]
    return prefix + ':' + digest


def native_account_key(account):
    platform = platform_key(account.get('platform'))
    return account_key('native', account['id'], platform, subjects(platform, account))


def read_native_accounts(workspace: Path) -> list[dict]:
    """Read only identity columns; never initialize a runtime or fetch cookies."""
    workspace = Path(workspace).resolve()
    path = workspace / '.easel/operations/operations.sqlite3'
    for candidate in (workspace / '.easel', path.parent, path):
        if candidate.is_symlink():
            raise AccountDirectoryError('账号目录路径无法核对，请保留已有数据后检查')
    if not path.exists():
        return []
    if not path.is_file() or not path.resolve().is_relative_to(workspace):
        raise AccountDirectoryError('账号目录路径无法核对，请保留已有数据后检查')
    try:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2) as database:
            database.row_factory = sqlite3.Row
            values = database.execute('''
                SELECT id, platform, nickname, avatar, status, uid, sec_uid,
                    (coalesce(storage_state,'')!='' OR coalesce(creator_storage_state,'')!=''
                        OR coalesce(cookie,'')!='') AS has_auth
                FROM douyinaccount ORDER BY id
            ''').fetchall()
            return [dict(value) for value in values]
    except (sqlite3.Error, OSError):
        raise AccountDirectoryError('暂时无法核对已有账号目录，请稍后重试；账号数据已保留') from None


def _capabilities(platform, catalog):
    values = {name: 'unavailable' for name in CAPABILITIES}
    if platform in _MANAGED:
        return {name: 'needs_verification' for name in CAPABILITIES}
    # A browser-session connector is not a publishing/interaction adapter.
    if catalog.get('backend') not in {'social-browser', 'unsupported'}:
        for name in ('creator', 'publish', 'analytics'):
            values[name] = 'needs_verification'
    declared = catalog.get('capabilities')
    if isinstance(declared, dict):
        for name in CAPABILITIES:
            if declared.get(name) in {'needs_verification', 'unavailable', 'unknown'}:
                values[name] = declared[name]
    return values


def build_account_directory(project_id, platforms, native_accounts=(), *, shared_sessions=None,
                            legacy_identities=None):
    """Combine saved accounts once, without trusting display names as identity.

    ``legacy_identities`` must already be freshness/generation checked by the
    Host. Even then the authority-issued platform IDs must match before dedup.
    """
    shared_sessions, legacy_identities = shared_sessions or {}, legacy_identities or {}
    catalog = {item['platform']: item for item in platforms}
    rows, matched_legacy = [], set()
    for native in native_accounts:
        identifier = native.get('id')
        if type(identifier) is not int or identifier < 1:
            raise AccountDirectoryError('已有账号编号无法核对，请保留数据后检查')
        platform = platform_key(native.get('platform'))
        if not isinstance(platform, str) or not platform:
            raise AccountDirectoryError('已有账号平台无法核对，请保留数据后检查')
        connector = catalog.get(platform, {'name': platform, 'backend': 'unsupported'})
        subject = subjects(platform, native)
        shared = shared_sessions.get(platform) or {}
        expected = subjects(platform, shared.get('identity'), require_verified=True)
        linked = shared.get('account_id') == identifier and matching_subjects(expected, subject)
        legacy = legacy_identities.get(platform) or {}
        legacy_match = bool(connector.get('loggedIn') and legacy.get('loggedIn') and matching_subjects(
            subjects(platform, legacy, require_verified=True), subject))
        if linked or legacy_match:
            matched_legacy.add(platform)
        active = native.get('status') == 'active' and bool(native.get('has_auth'))
        capabilities = _capabilities(platform, connector)
        if linked:
            for name in CAPABILITIES:
                value = (shared.get('capabilities') or {}).get(name)
                if value in _STATES:
                    capabilities[name] = value
                elif type(value) is bool:
                    capabilities[name] = 'verified' if value else 'needs_verification'
        retained_login = legacy_match or linked and any(value == 'verified' for value in capabilities.values())
        rows.append({
            'key': native_account_key(native), 'platform': platform,
            'platformName': connector.get('name') or platform,
            'name': native.get('nickname') if subject and isinstance(native.get('nickname'), str) and native['nickname'] else (connector.get('name') or platform) + '账号',
            'avatar': native.get('avatar') if subject and isinstance(native.get('avatar'), str) else '',
            'loggedIn': bool(active or retained_login),
            'status': 'logged_in' if active else 'unknown' if retained_login else 'needs_login',
            'nativeAccountId': identifier, 'current': bool(linked or legacy_match),
            'identityVerified': bool(linked or legacy_match), 'capabilities': capabilities,
        })
    for platform, connector in catalog.items():
        if not connector.get('loggedIn') or platform in matched_legacy:
            continue
        identity = legacy_identities.get(platform) or {}
        verified = identity.get('loggedIn') and bool(subjects(platform, identity, require_verified=True))
        rows.append({
            'key': account_key('legacy', platform, platform, subjects(platform, identity, require_verified=True)), 'platform': platform,
            'platformName': connector.get('name') or platform,
            'name': identity.get('name') if verified and isinstance(identity.get('name'), str) and identity['name'] else (connector.get('name') or platform) + '账号',
            'avatar': identity.get('avatar') if verified and isinstance(identity.get('avatar'), str) else '',
            'loggedIn': True, 'status': 'logged_in' if verified else 'unknown',
            'nativeAccountId': None, 'current': True, 'identityVerified': bool(verified),
            'capabilities': _capabilities(platform, connector),
        })
    return {'project_id': project_id, 'platforms': deepcopy(platforms), 'accounts': rows}
