"""Local identities, opaque sessions and per-IP memberships backed by SQLite.

The HTTP layer authenticates a cookie into a user. This store never treats a
username, role or identity header supplied by a browser as authentication.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid

from fastapi import HTTPException


SESSION_COOKIE = "easel_session"
SESSION_SECONDS = 12 * 60 * 60
PASSWORD_ITERATIONS = 600_000
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_FAILURE_LIMIT = 6
ROLES = frozenset({"owner", "operator", "viewer"})
COLLABORATOR_ROLES = frozenset({"operator", "viewer"})
_schema_lock = threading.Lock()


def _password_hash(password: str) -> str:
    if not isinstance(password, str) or not 10 <= len(password) <= 1024:
        raise HTTPException(400, "密码需为 10–1024 个字符")
    salt = secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str | None) -> bool:
    # A missing user performs the same expensive derivation as an existing one.
    encoded = encoded or f"pbkdf2_sha256${PASSWORD_ITERATIONS}$" + "00" * 24 + "$" + "00" * 32
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != PASSWORD_ITERATIONS:
            return False
        value = password if isinstance(password, str) and len(password) <= 1024 else ""
        actual = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def _username(value: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(400, "用户名格式无效")
    normalized = value.strip().casefold()
    if not 3 <= len(normalized) <= 64 or any(character.isspace() or ord(character) < 32 for character in normalized):
        raise HTTPException(400, "用户名需为 3–64 个字符，不能包含空格或控制字符")
    return normalized


def _display_name(value: str, fallback: str = "") -> str:
    if not isinstance(value, str) or len(value.strip()) > 80 or any(ord(character) < 32 for character in value):
        raise HTTPException(400, "显示名称需为 1–80 个字符")
    return value.strip() or fallback


def _public_user(row) -> dict:
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
            "is_admin": bool(row["is_admin"]), "active": bool(row["active"]),
            "created_at": row["created_at"]}


class AccessStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        directory = self.root / ".easel"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "access.sqlite3"
        with _schema_lock:
            with self._connect() as database:
                database.execute("PRAGMA journal_mode=WAL")
                database.executescript("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        display_name TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        is_admin INTEGER NOT NULL DEFAULT 0 CHECK(is_admin IN (0,1)),
                        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                        created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                    CREATE TABLE IF NOT EXISTS memberships (
                        project_id TEXT NOT NULL,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        role TEXT NOT NULL CHECK(role IN ('owner','operator','viewer')),
                        created_at REAL NOT NULL,
                        PRIMARY KEY(project_id, user_id)
                    );
                    CREATE INDEX IF NOT EXISTS memberships_user ON memberships(user_id);
                    CREATE TABLE IF NOT EXISTS login_attempts (
                        identifier TEXT PRIMARY KEY,
                        failures INTEGER NOT NULL,
                        window_start REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS access_audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        at REAL NOT NULL,
                        actor_id TEXT,
                        action TEXT NOT NULL,
                        project_id TEXT,
                        target_id TEXT
                    );
                    CREATE INDEX IF NOT EXISTS access_audit_project ON access_audit(project_id,id);
                    CREATE TABLE IF NOT EXISTS project_ownership (
                        project_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL REFERENCES users(id),
                        created_by TEXT NOT NULL REFERENCES users(id),
                        created_at REAL NOT NULL
                    );
                """)
                if 'details' not in {row['name'] for row in database.execute('PRAGMA table_info(access_audit)')}:
                    database.execute("ALTER TABLE access_audit ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")
            self._migrate_ownership()
            self.path.chmod(0o600)

    def _migrate_ownership(self):
        """Retain the earliest active legacy owner and preserve everyone as members."""
        with self._connect(immediate=True) as database:
            indexed = database.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='memberships_single_owner'").fetchone()
            missing = database.execute("""SELECT 1 FROM memberships m LEFT JOIN project_ownership p
                ON p.project_id=m.project_id WHERE m.role='owner' AND p.project_id IS NULL LIMIT 1""").fetchone()
            if indexed and not missing:
                return
            rows = database.execute("""SELECT m.*,u.active FROM memberships m JOIN users u ON u.id=m.user_id
                WHERE m.role='owner' ORDER BY m.project_id,u.active DESC,m.created_at,u.created_at,u.id""").fetchall()
            grouped = {}
            for row in rows:
                grouped.setdefault(row['project_id'], []).append(row)
            for project_id, owners in grouped.items():
                prior = database.execute('SELECT * FROM project_ownership WHERE project_id=?', (project_id,)).fetchone()
                chosen = next((row for row in owners if prior and row['user_id'] == prior['owner_id'] and row['active']), owners[0])
                database.execute("UPDATE memberships SET role='operator' WHERE project_id=? AND role='owner' AND user_id<>?",
                                 (project_id, chosen['user_id']))
                database.execute("""INSERT INTO project_ownership VALUES(?,?,?,?)
                    ON CONFLICT(project_id) DO UPDATE SET owner_id=excluded.owner_id""",
                    (project_id, chosen['user_id'], prior['created_by'] if prior else chosen['user_id'], chosen['created_at']))
                if len(owners) > 1:
                    self._audit(database, None, 'ownership.migrate', project_id, chosen['user_id'],
                                {'previous_owners': [row['user_id'] for row in owners]})
            database.execute("CREATE UNIQUE INDEX IF NOT EXISTS memberships_single_owner ON memberships(project_id) WHERE role='owner'")

    @contextmanager
    def _connect(self, *, immediate: bool = False):
        database = sqlite3.connect(self.path, timeout=20)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA busy_timeout=20000")
        try:
            if immediate:
                database.execute("BEGIN IMMEDIATE")
            yield database
            database.commit()
        except BaseException:
            database.rollback()
            raise
        finally:
            database.close()

    def configured(self) -> bool:
        with self._connect() as database:
            return database.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    @staticmethod
    def _new_session(database, user_id: str) -> str:
        token = secrets.token_urlsafe(48)
        now = time.time()
        database.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        database.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                         (hashlib.sha256(token.encode()).hexdigest(), user_id, now, now + SESSION_SECONDS))
        return token

    def setup(self, username: str, password: str, display_name: str = "", project_ids=()) -> tuple[dict, str]:
        username = _username(username)
        name = _display_name(display_name, username)
        password_hash = _password_hash(password)
        with self._connect(immediate=True) as database:
            if database.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise HTTPException(409, "管理员已初始化，请直接登录")
            identifier, now = uuid.uuid4().hex, time.time()
            database.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)",
                             (identifier, username, name, password_hash, 1, 1, now))
            for project_id in set(project_ids):
                database.execute("INSERT INTO memberships VALUES(?,?,?,?)", (project_id, identifier, "owner", now))
                database.execute("INSERT INTO project_ownership VALUES(?,?,?,?)", (project_id, identifier, identifier, now))
            token = self._new_session(database, identifier)
            row = database.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()
            return _public_user(row), token

    def register(self, username: str, password: str, display_name: str = '', *, peer: str = '') -> tuple[dict, str]:
        """Self-service users start with zero memberships and no platform privileges."""
        key = hashlib.sha256(f'register\0{peer}'.encode()).hexdigest()
        # Count both completed and rejected registrations, before expensive hashing.
        with self._connect(immediate=True) as database:
            now = time.time()
            if not database.execute('SELECT 1 FROM users WHERE active=1 AND is_admin=1 LIMIT 1').fetchone():
                raise HTTPException(503, '平台尚未初始化，请联系管理员')
            database.execute('DELETE FROM login_attempts WHERE window_start < ?', (now - LOGIN_WINDOW_SECONDS,))
            attempt = database.execute('SELECT failures FROM login_attempts WHERE identifier=?', (key,)).fetchone()
            if attempt and attempt['failures'] >= LOGIN_FAILURE_LIMIT:
                raise HTTPException(429, '注册尝试过多，请在 15 分钟后重试', headers={'Retry-After': '900'})
            database.execute('''INSERT INTO login_attempts VALUES(?,?,?)
                ON CONFLICT(identifier) DO UPDATE SET failures=failures+1''', (key, 1, now))
        normalized = _username(username)
        name = _display_name(display_name, normalized)
        password_hash = _password_hash(password)
        identifier = uuid.uuid4().hex
        try:
            with self._connect(immediate=True) as database:
                database.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',
                                 (identifier, normalized, name, password_hash, 0, 1, time.time()))
                token = self._new_session(database, identifier)
                self._audit(database, identifier, 'auth.register')
                return _public_user(database.execute('SELECT * FROM users WHERE id=?', (identifier,)).fetchone()), token
        except sqlite3.IntegrityError:
            raise HTTPException(409, '用户名已存在，请登录或换一个用户名') from None

    def login(self, username: str, password: str, *, peer: str = "") -> tuple[dict, str]:
        # Invalid usernames receive the same response and derivation as unknown users.
        try:
            normalized = _username(username)
        except HTTPException:
            normalized = ""
        key = hashlib.sha256(f"{peer}\0{normalized}".encode()).hexdigest()
        error = None
        result = None
        with self._connect(immediate=True) as database:
            now = time.time()
            database.execute("DELETE FROM login_attempts WHERE window_start < ?", (now - LOGIN_WINDOW_SECONDS,))
            attempt = database.execute("SELECT * FROM login_attempts WHERE identifier=?", (key,)).fetchone()
            if attempt and attempt["failures"] >= LOGIN_FAILURE_LIMIT:
                raise HTTPException(429, "登录尝试过多，请在 15 分钟后重试", headers={"Retry-After": "900"})
            row = database.execute("SELECT * FROM users WHERE username=?", (normalized,)).fetchone()
            valid = _password_matches(password, row["password_hash"] if row else None)
            if not valid or not row or not row["active"]:
                database.execute("""INSERT INTO login_attempts VALUES(?,?,?)
                    ON CONFLICT(identifier) DO UPDATE SET failures=failures+1""", (key, 1, now))
                error = HTTPException(401, "用户名或密码不正确")
            else:
                database.execute("DELETE FROM login_attempts WHERE identifier=?", (key,))
                result = (_public_user(row), self._new_session(database, row["id"]))
        # Raise after commit so failed attempts are actually persisted.
        if error:
            raise error
        return result

    def authenticate_token(self, raw: str | None) -> dict | None:
        if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", raw):
            return None
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        with self._connect() as database:
            row = database.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""", (token_hash, time.time())).fetchone()
            return _public_user(row) if row else None

    def logout(self, raw: str | None):
        if not isinstance(raw, str):
            return
        with self._connect() as database:
            database.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(raw.encode()).hexdigest(),))

    def user(self, user_id: str) -> dict | None:
        with self._connect() as database:
            row = database.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return _public_user(row) if row else None

    def users(self) -> list[dict]:
        with self._connect() as database:
            return [_public_user(row) for row in database.execute("SELECT * FROM users ORDER BY created_at,id")]

    def directory(self) -> list[dict]:
        return [{key: user[key] for key in ("id", "username", "display_name")}
                for user in self.users() if user["active"]]

    @staticmethod
    def _require_actor(database, actor_id: str | None, *, project_id: str | None = None, admin: bool = False):
        if actor_id is None:
            return  # Trusted internal migration/setup call, never a browser payload.
        actor = database.execute("SELECT active,is_admin FROM users WHERE id=?", (actor_id,)).fetchone()
        if not actor or not actor["active"]:
            raise HTTPException(401, "登录已失效，请重新登录")
        if admin and not actor["is_admin"]:
            raise HTTPException(403, "需要平台管理员权限")
        if project_id is not None:
            membership = database.execute("SELECT role FROM memberships WHERE project_id=? AND user_id=?",
                                          (project_id, actor_id)).fetchone()
            if not membership or membership["role"] != "owner":
                raise HTTPException(403, "需要当前 IP 的 owner 权限")

    def create_user(self, username: str, password: str, display_name: str = "", *,
                    is_admin: bool = False, actor_id: str | None = None) -> dict:
        username = _username(username)
        name = _display_name(display_name, username)
        password_hash = _password_hash(password)
        identifier, now = uuid.uuid4().hex, time.time()
        try:
            with self._connect(immediate=True) as database:
                self._require_actor(database, actor_id, admin=True)
                database.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)",
                                 (identifier, username, name, password_hash, int(is_admin), 1, now))
                return _public_user(database.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone())
        except sqlite3.IntegrityError:
            raise HTTPException(409, "用户名已存在") from None

    @staticmethod
    def _active_owners(database, project_id: str) -> int:
        return database.execute("""SELECT count(*) FROM memberships m JOIN users u ON m.user_id=u.id
            WHERE m.project_id=? AND m.role='owner' AND u.active=1""", (project_id,)).fetchone()[0]

    def update_user(self, user_id: str, *, display_name: str | None = None,
                    active: bool | None = None, password: str | None = None,
                    is_admin: bool | None = None, actor_id: str | None = None) -> dict:
        password_hash = _password_hash(password) if password is not None else None
        with self._connect(immediate=True) as database:
            self._require_actor(database, actor_id, admin=True)
            row = database.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise HTTPException(404, "用户不存在")
            if active is False and row["active"]:
                owned = database.execute("SELECT project_id FROM memberships WHERE user_id=? AND role='owner'", (user_id,))
                if any(self._active_owners(database, item["project_id"]) <= 1 for item in owned):
                    raise HTTPException(409, "该用户拥有 IP 项目，请先转移项目所有权")
            if row["active"] and row["is_admin"] and (active is False or is_admin is False):
                count = database.execute("SELECT count(*) FROM users WHERE active=1 AND is_admin=1").fetchone()[0]
                if count <= 1:
                    raise HTTPException(409, "不能禁用或降级最后一位平台管理员")
            values = {"display_name": _display_name(display_name, row["username"]) if display_name is not None else row["display_name"],
                      "active": int(active) if active is not None else row["active"],
                      "is_admin": int(is_admin) if is_admin is not None else row["is_admin"],
                      "password_hash": password_hash or row["password_hash"]}
            database.execute("UPDATE users SET display_name=?,active=?,is_admin=?,password_hash=? WHERE id=?",
                             (*values.values(), user_id))
            if password_hash or active is False or is_admin is not None:
                database.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return _public_user(database.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def role(self, user_id: str, project_id: str) -> str | None:
        with self._connect() as database:
            row = database.execute("""SELECT m.role FROM memberships m JOIN users u ON m.user_id=u.id
                WHERE m.user_id=? AND m.project_id=? AND u.active=1""", (user_id, project_id)).fetchone()
            return row["role"] if row else None

    def require_role(self, user_id: str, project_id: str, allowed) -> str:
        role = self.role(user_id, project_id)
        if role is None:
            raise HTTPException(404, "IP 不存在或没有访问权限")
        if role not in allowed:
            raise HTTPException(403, "当前角色没有此操作权限")
        return role

    def members(self, project_id: str) -> list[dict]:
        with self._connect() as database:
            rows = database.execute("""SELECT m.user_id,m.role,u.username,u.display_name,u.active,u.is_admin
                FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.project_id=?
                ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'operator' THEN 1 ELSE 2 END,m.created_at,u.username""", (project_id,))
            return [{**dict(row), "active": bool(row["active"]), "is_admin": bool(row["is_admin"])} for row in rows]

    def project_access(self, project_id: str) -> dict:
        with self._connect() as database:
            row = database.execute('''SELECT p.*,o.username AS owner_username,o.display_name AS owner_name,
                c.username AS creator_username,c.display_name AS creator_name,
                (SELECT count(*) FROM memberships m WHERE m.project_id=p.project_id) AS member_count
                FROM project_ownership p JOIN users o ON o.id=p.owner_id JOIN users c ON c.id=p.created_by
                WHERE p.project_id=?''', (project_id,)).fetchone()
            if not row:
                raise HTTPException(409, '项目所有权记录缺失，请联系管理员检查迁移')
            return {'owner': {'id': row['owner_id'], 'username': row['owner_username'], 'display_name': row['owner_name']},
                    'created_by': {'id': row['created_by'], 'username': row['creator_username'], 'display_name': row['creator_name']},
                    'member_count': row['member_count'], 'visibility': 'private'}

    def set_member(self, project_id: str, user_id: str, role: str, *, actor_id: str | None = None) -> dict:
        if role not in ROLES:
            raise HTTPException(400, "角色必须为 owner、operator 或 viewer")
        with self._connect(immediate=True) as database:
            self._require_actor(database, actor_id, project_id=project_id)
            user = database.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user or not user["active"]:
                raise HTTPException(404, "用户不存在或已停用")
            current = database.execute("SELECT role FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
            if current and current['role'] == 'owner':
                if role == 'owner':
                    return {**{key: user[key] for key in ('username', 'display_name')}, 'user_id': user_id,
                            'role': 'owner', 'active': bool(user['active']), 'is_admin': bool(user['is_admin'])}
                raise HTTPException(409, 'Owner 不能通过成员角色变更，请先转移项目所有权')
            if role == 'owner':
                # Only trusted creation/migration may establish the initial owner.
                if actor_id is not None or database.execute('SELECT 1 FROM project_ownership WHERE project_id=?', (project_id,)).fetchone():
                    raise HTTPException(409, '每个 IP 只有一位 Owner，请使用所有权转移')
                database.execute('INSERT INTO project_ownership VALUES(?,?,?,?)', (project_id, user_id, user_id, time.time()))
            database.execute("""INSERT INTO memberships VALUES(?,?,?,?)
                ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role""", (project_id, user_id, role, time.time()))
            if actor_id is not None:
                self._audit(database, actor_id, 'member.role_change' if current else 'member.add', project_id, user_id,
                            {'from_role': current['role'], 'role': role} if current else {'role': role})
            return {"user_id": user_id, "username": user["username"], "display_name": user["display_name"],
                    "role": role, "active": bool(user["active"]), "is_admin": bool(user["is_admin"])}

    def add_owner(self, project_id: str, user_id: str):
        return self.set_member(project_id, user_id, "owner")

    def transfer_owner(self, project_id: str, user_id: str, *, actor_id: str, expected_owner_id: str):
        with self._connect(immediate=True) as database:
            self._require_actor(database, actor_id, project_id=project_id)
            ownership = database.execute('SELECT owner_id FROM project_ownership WHERE project_id=?', (project_id,)).fetchone()
            if not ownership or ownership['owner_id'] != expected_owner_id or expected_owner_id != actor_id:
                raise HTTPException(409, '项目 Owner 已变化，请刷新后重新确认')
            if user_id == actor_id:
                raise HTTPException(400, '请选择其他成员接收项目')
            target = database.execute('''SELECT m.role,u.active FROM memberships m JOIN users u ON u.id=m.user_id
                WHERE m.project_id=? AND m.user_id=?''', (project_id, user_id)).fetchone()
            if not target or not target['active']:
                raise HTTPException(409, '请先将有效用户添加为项目成员，再转移所有权')
            database.execute("UPDATE memberships SET role='operator' WHERE project_id=? AND user_id=?", (project_id, actor_id))
            database.execute("UPDATE memberships SET role='owner' WHERE project_id=? AND user_id=?", (project_id, user_id))
            database.execute('UPDATE project_ownership SET owner_id=? WHERE project_id=?', (user_id, project_id))
            self._audit(database, actor_id, 'ownership.transfer', project_id, user_id, {'previous_owner_id': actor_id})
        return self.project_access(project_id)

    def leave_project(self, project_id: str, *, actor_id: str):
        with self._connect(immediate=True) as database:
            self._require_actor(database, actor_id)
            membership = database.execute('SELECT role FROM memberships WHERE project_id=? AND user_id=?', (project_id, actor_id)).fetchone()
            if not membership:
                raise HTTPException(404, 'IP 不存在或没有访问权限')
            if membership['role'] == 'owner':
                raise HTTPException(409, 'Owner 需要先转移项目所有权才能退出')
            database.execute('DELETE FROM memberships WHERE project_id=? AND user_id=?', (project_id, actor_id))
            self._audit(database, actor_id, 'member.leave', project_id, actor_id, {'role': membership['role']})

    def remove_member(self, project_id: str, user_id: str, *, actor_id: str | None = None):
        with self._connect(immediate=True) as database:
            self._require_actor(database, actor_id, project_id=project_id)
            current = database.execute("""SELECT m.role,u.active FROM memberships m JOIN users u ON m.user_id=u.id
                WHERE m.project_id=? AND m.user_id=?""", (project_id, user_id)).fetchone()
            if not current:
                raise HTTPException(404, "成员不存在")
            if current['role'] == 'owner':
                raise HTTPException(409, '不能移除项目 Owner，请先转移所有权')
            database.execute("DELETE FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id))
            if actor_id is not None:
                self._audit(database, actor_id, 'member.remove', project_id, user_id, {'role': current['role']})

    def remove_project(self, project_id: str):
        with self._connect(immediate=True) as database:
            database.execute("DELETE FROM memberships WHERE project_id=?", (project_id,))
            database.execute('DELETE FROM project_ownership WHERE project_id=?', (project_id,))

    @staticmethod
    def _audit(database, actor_id=None, action='', project_id=None, target_id=None, details=None):
        database.execute('INSERT INTO access_audit(at,actor_id,action,project_id,target_id,details) VALUES(?,?,?,?,?,?)',
                         (time.time(), actor_id, action[:100], project_id, target_id, json.dumps(details or {}, ensure_ascii=False)))

    def activity(self, project_id: str, *, actor_id: str, before: int | None = None, limit: int = 30):
        limit = max(1, min(limit, 100))
        with self._connect() as database:
            self._require_actor(database, actor_id, project_id=project_id)
            rows = database.execute('''SELECT a.*,u.username AS actor_username,u.display_name AS actor_name,
                t.username AS target_username,t.display_name AS target_name FROM access_audit a
                LEFT JOIN users u ON u.id=a.actor_id LEFT JOIN users t ON t.id=a.target_id
                WHERE a.project_id=? AND (? IS NULL OR a.id<?) ORDER BY a.id DESC LIMIT ?''',
                (project_id, before, before, limit + 1)).fetchall()
            items = [{'id': row['id'], 'at': row['at'], 'project_id': row['project_id'], 'action': row['action'],
                      'actor': {'id': row['actor_id'], 'username': row['actor_username'], 'display_name': row['actor_name']},
                      'target': {'id': row['target_id'], 'username': row['target_username'], 'display_name': row['target_name']},
                      'details': json.loads(row['details'])} for row in rows[:limit]]
            return {'items': items, 'next_cursor': items[-1]['id'] if len(rows) > limit else None}

    def audit(self, actor_id: str | None = None, action: str = "", project_id: str | None = None,
              target_id: str | None = None, details: dict | None = None):
        with self._connect() as database:
            self._audit(database, actor_id, action, project_id, target_id, details)
