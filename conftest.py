import sys
import sqlite3
import threading
from pathlib import Path

import pytest


# Runtime IP spaces link back to shared skills; collect the owning source once.
collect_ignore = ["projects"]


@pytest.fixture(autouse=True)
def isolate_web_outputs(request, tmp_path_factory, monkeypatch):
    if not request.node.path.is_relative_to(Path(__file__).parent / "tests"):
        return
    outputs = tmp_path_factory.mktemp("isolated-web-outputs")
    for name in ("app", "web.app"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "OUTPUTS_DIR"):
            monkeypatch.setattr(module, "OUTPUTS_DIR", outputs)
            if hasattr(module, 'OperationsManager'):
                from easel.operations import OperationsManager
                operations_root = outputs / 'native-runtime-fixture'
                monkeypatch.setattr(module, 'OperationsManager', lambda _root: OperationsManager(operations_root))
                monkeypatch.setattr(module.app.state, 'operations_manager', None, raising=False)


def pytest_configure(config):
    config.addinivalue_line("markers", "access_integration: exercise authentication without legacy owner-session fixtures")


@pytest.fixture(scope="session")
def _legacy_owner_template(tmp_path_factory):
    """One real password derivation; each legacy test still gets its own database."""
    from easel.access import AccessStore

    root = tmp_path_factory.mktemp("legacy-owner-template")
    store = AccessStore(root)
    owner, _ = store.setup("legacy-test-owner", "Legacy fixture password 84", "Legacy test owner", ["default"])
    return store.path, owner["id"]


@pytest.fixture(autouse=True)
def authenticate_legacy_test_clients(request, tmp_path_factory, monkeypatch, isolate_web_outputs):
    """Preserve legacy API tests through the same real login/session RBAC boundary.

    New access-control suites opt out and test actual anonymous/login behavior.
    No middleware, role lookup or production authorization result is replaced.
    Store construction is redirected only within a legacy test after its first
    TestClient is created, so tests cannot register users in the real repository.
    """
    path = request.node.path
    excluded = {"test_access.py", "test_access_control.py", "test_multiuser_integration.py",
                "test_workspace_media_security.py"}
    if (not path.is_relative_to(Path(__file__).parent / "tests")
            or path.name in excluded or request.node.get_closest_marker("access_integration")):
        return

    from fastapi.testclient import TestClient

    original_client_init = TestClient.__init__
    initialized = {}
    initialize_lock = threading.RLock()

    def client_init(test_client, *args, **kwargs):
        from easel.access import AccessStore, SESSION_COOKIE

        with initialize_lock:
            if not initialized:
                template_path, owner_id = request.getfixturevalue("_legacy_owner_template")
                root = tmp_path_factory.mktemp("legacy-access")
                directory = root / ".easel"
                directory.mkdir(mode=0o700)
                # SQLite backup includes any committed WAL pages without copying
                # an open database or sharing mutable identities between tests.
                with sqlite3.connect(template_path) as source:
                    with sqlite3.connect(directory / "access.sqlite3") as destination:
                        source.backup(destination)
                original_store_init = AccessStore.__init__

                def isolated_store_init(store, _requested_root):
                    original_store_init(store, root)

                monkeypatch.setattr(AccessStore, "__init__", isolated_store_init)
                initialized.update(root=root, owner_id=owner_id)
            store = AccessStore(initialized["root"])
            with store._connect(immediate=True) as database:
                token = store._new_session(database, initialized["owner_id"])

        original_client_init(test_client, *args, **kwargs)
        # Explicit authentication supplied by a legacy case takes precedence.
        if SESSION_COOKIE not in test_client.cookies:
            test_client.cookies.set(SESSION_COOKIE, token)

    monkeypatch.setattr(TestClient, "__init__", client_init)
