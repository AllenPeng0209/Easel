"""Real SQLite/password/session regressions for IP member access."""
from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
from pathlib import Path
import sqlite3
import time
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest


PASSWORD = secrets.token_urlsafe(24)


@pytest.fixture
def store(tmp_path):
    assert find_spec("easel.access") is not None, "Multi-user access store is not implemented"
    from easel.access import AccessStore
    return AccessStore(tmp_path)


def initialize(store):
    return store.setup("owner", PASSWORD, "IP owner", ["default", "other-ip"])


def test_setup_hashes_password_and_session_and_claims_existing_ips(store):
    user, token = initialize(store)
    assert user["is_admin"] and user["active"]
    assert store.role(user["id"], "default") == "owner"
    assert store.role(user["id"], "other-ip") == "owner"
    assert store.authenticate_token(token)["id"] == user["id"]
    with sqlite3.connect(store.path) as db:
        password_hash = db.execute("select password_hash from users").fetchone()[0]
        session_hash = db.execute("select token_hash from sessions").fetchone()[0]
    assert PASSWORD not in password_hash
    assert token != session_hash and token not in store.path.read_bytes().decode("latin1")
    assert "password_hash" not in user
    assert len(token) >= 40


def test_only_one_concurrent_initial_setup_can_claim_admin(store):
    def setup(index):
        try:
            return store.setup(f"owner{index}", PASSWORD, "Owner", ["default"])[0]
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(setup, [1, 2]))
    assert len([value for value in results if isinstance(value, dict)]) == 1
    assert results.count(409) == 1


def test_invalid_password_or_username_never_creates_a_session(store):
    initialize(store)
    for username, password in [("owner", "wrong password"), ("missing", PASSWORD)]:
        with pytest.raises(HTTPException) as error:
            store.login(username, password, peer="test-peer")
        assert error.value.status_code == 401
    assert len(store.users()) == 1


def test_login_rate_limit_rejects_password_guessing_without_logging_out_valid_sessions(store):
    owner, token = initialize(store)
    for attempt in range(6):
        with pytest.raises(HTTPException):
            store.login("owner", "wrong password", peer="guessing-peer")
    with pytest.raises(HTTPException) as error:
        store.login("owner", PASSWORD, peer="guessing-peer")
    assert error.value.status_code == 429
    assert store.authenticate_token(token)["id"] == owner["id"]
    assert store.login("owner", PASSWORD, peer="another-peer")[0]["id"] == owner["id"]


def test_password_reset_disable_and_logout_revoke_sessions(store):
    replacement = secrets.token_urlsafe(24)
    initialize(store)
    user = store.create_user("operator", PASSWORD, "Operator")
    _, first = store.login("operator", PASSWORD)
    store.update_user(user["id"], password=replacement)
    assert store.authenticate_token(first) is None
    _, second = store.login("operator", replacement)
    store.logout(second)
    assert store.authenticate_token(second) is None
    _, third = store.login("operator", replacement)
    store.update_user(user["id"], active=False)
    assert store.authenticate_token(third) is None
    with pytest.raises(HTTPException):
        store.login("operator", replacement)


def test_expired_or_unknown_token_is_rejected(store):
    _, token = initialize(store)
    with sqlite3.connect(store.path) as db:
        db.execute("update sessions set expires_at = ?", (time.time() - 5,))
    assert store.authenticate_token(token) is None
    assert store.authenticate_token("not-a-session") is None


def test_membership_is_per_ip_and_roles_are_enforced(store):
    initialize(store)
    user = store.create_user("team-member", PASSWORD, "Member")
    store.set_member("default", user["id"], "operator")
    store.set_member("other-ip", user["id"], "viewer")
    assert store.require_role(user["id"], "default", {"owner", "operator"}) == "operator"
    with pytest.raises(HTTPException) as error:
        store.require_role(user["id"], "other-ip", {"owner", "operator"})
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        store.require_role(user["id"], "unknown-ip", {"viewer"})
    assert error.value.status_code == 404
    assert store.members("default")[1]["user_id"] == user["id"]


def test_last_owner_cannot_be_removed_demoted_or_disabled(store):
    user, _ = initialize(store)
    for action in [lambda: store.remove_member("default", user["id"]),
                   lambda: store.set_member("default", user["id"], "viewer"),
                   lambda: store.update_user(user["id"], active=False)]:
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 409
    assert store.role(user["id"], "default") == "owner"
    assert store.authenticate_token(store.login("owner", PASSWORD)[1])


def test_owner_cannot_be_replaced_through_concurrent_member_assignments(store):
    first, _ = initialize(store)
    second = store.create_user("second-owner", PASSWORD, "Second")
    def demote(identifier):
        try:
            store.set_member("default", identifier, "owner", actor_id=first['id'])
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(demote, [first["id"], second["id"]]))
    assert sorted(codes) == [200, 409]
    assert sum(member["role"] == "owner" for member in store.members("default")) == 1


def test_invalid_roles_duplicate_names_and_short_password_are_rejected(store):
    user, _ = initialize(store)
    for action in [lambda: store.set_member("default", user["id"], "admin"),
                   lambda: store.create_user("OWNER", PASSWORD),
                   lambda: store.create_user("someone", "short")]:
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code in {400, 409}


def test_membership_mutation_rechecks_owner_after_waiting_for_database_lock(store):
    import threading
    owner, _ = initialize(store)
    replacement = store.create_user("new-owner", PASSWORD)
    target = store.create_user("target-user", PASSWORD)
    store.set_member("default", replacement["id"], "operator")
    started = threading.Event()
    def delayed_grant():
        started.set()
        try:
            store.set_member("default", target["id"], "operator", actor_id=owner["id"])
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=1) as pool:
        with sqlite3.connect(store.path) as database:
            database.execute("BEGIN IMMEDIATE")
            pending = pool.submit(delayed_grant)
            assert started.wait(2)
            database.execute("UPDATE memberships SET role='viewer' WHERE project_id='default' AND user_id=?", (owner["id"],))
            database.execute("UPDATE memberships SET role='owner' WHERE project_id='default' AND user_id=?", (replacement['id'],))
            database.execute("UPDATE project_ownership SET owner_id=? WHERE project_id='default'", (replacement['id'],))
        assert pending.result(timeout=3) == 403
    assert store.role(target["id"], "default") is None


def test_non_admin_store_actor_cannot_create_or_update_users(store):
    initialize(store)
    member = store.create_user("member", PASSWORD)
    for action in [lambda: store.create_user("intruder", PASSWORD, actor_id=member["id"]),
                   lambda: store.update_user(member["id"], is_admin=True, actor_id=member["id"])]:
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 403
    assert not store.user(member["id"])["is_admin"]


@pytest.mark.parametrize("payload", [
    {"current_password": PASSWORD, "new_password": "A new secure password 17"},
    {"current_password": PASSWORD, "new_password": "A new secure password 17", "user_id": "someone-else"},
])
def test_removed_self_password_route_cannot_change_credentials_or_sessions(client, store, payload):
    owner = setup_request(client).json()["user"]
    before = client.cookies.get("easel_session")
    response = client.post("/api/auth/password", json=payload)
    assert response.status_code == 404
    assert client.cookies.get("easel_session") == before
    assert store.authenticate_token(before)["id"] == owner["id"]
    assert client.get("/api/auth/me").json()["id"] == owner["id"]
    assert store.login("owner", PASSWORD)[0]["id"] == owner["id"]
    with pytest.raises(HTTPException) as error:
        store.login("owner", "A new secure password 17")
    assert error.value.status_code == 401


def test_administrator_can_still_reset_a_users_password_and_revoke_old_sessions(client, store):
    setup_request(client)
    created = client.post("/api/users", json={"username": "operator", "password": PASSWORD,
                                               "display_name": "Operator"})
    assert created.status_code == 200, created.text
    identifier = created.json()["id"]
    _, old_session = store.login("operator", PASSWORD)
    response = client.patch(f"/api/users/{identifier}", json={"password": "Administrator reset password 23"})
    assert response.status_code == 200, response.text
    assert store.authenticate_token(old_session) is None
    with pytest.raises(HTTPException) as error:
        store.login("operator", PASSWORD)
    assert error.value.status_code == 401
    assert store.login("operator", "Administrator reset password 23")[0]["id"] == identifier


@pytest.fixture
def client(store):
    assert find_spec("web.access_routes") is not None, "Authentication routes are not implemented"
    from web.access_routes import create_router
    app = FastAPI()
    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.user = store.authenticate_token(request.cookies.get("easel_session", ""))
        return await call_next(request)
    app.include_router(create_router(lambda: store.root,
                                    lambda: [{"id": "default"}, {"id": "other-ip"}]))
    return TestClient(app, base_url="http://localhost:7860", client=("127.0.0.1", 40000),
                      headers={"Origin": "http://localhost:7860"})


def setup_request(client):
    response = client.post("/api/auth/setup", json={"username": "owner", "password": PASSWORD,
                                                   "display_name": "Owner"})
    assert response.status_code == 200, response.text
    return response


def test_http_setup_cookie_login_logout_and_bootstrap(client):
    assert client.get("/api/auth/bootstrap").json() == {"configured": False}
    setup = setup_request(client)
    cookie = setup.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert client.get("/api/auth/me").json()["username"] == "owner"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 200
    assert client.get("/api/auth/bootstrap").json() == {"configured": True}


def test_anonymous_and_non_admin_cannot_manage_users_or_owners(client):
    assert client.get("/api/users").status_code == 401
    setup_request(client)
    response = client.post("/api/users", json={"username": "viewer", "password": PASSWORD,
                                               "display_name": "Viewer"})
    assert response.status_code == 200, response.text
    user_id = response.json()["id"]
    assert client.post("/api/projects/default/members", json={"user_id": user_id, "role": "viewer"}).status_code == 200
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "viewer", "password": PASSWORD})
    assert client.get("/api/users").status_code == 403
    assert client.patch(f"/api/projects/default/members/{user_id}", json={"role": "owner"}).status_code == 403
    assert client.get("/api/projects/other-ip/members").status_code == 404
    directory = client.get("/api/users/directory").json()
    assert directory and set(directory[0]) == {"id", "username", "display_name"}


def test_no_identity_header_can_impersonate_owner(client):
    response = setup_request(client)
    user_id = response.json()["user"]["id"]
    client.post("/api/auth/logout")
    assert client.get("/api/users", headers={"X-User-ID": user_id, "X-Role": "owner"}).status_code == 401


def test_cross_origin_write_and_remote_initial_setup_are_rejected(client, store):
    response = client.post("/api/auth/setup", json={"username": "owner", "password": PASSWORD},
                           headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403
    assert not store.configured()
