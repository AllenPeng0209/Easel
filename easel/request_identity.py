"""Server-verified request identity, propagated into async jobs and copied threads."""
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import MutableMapping, MutableSet

_actor = ContextVar("easel_authenticated_actor", default=None)


def current_user() -> dict | None:
    return _actor.get()


@contextmanager
def actor_context(user: dict | None):
    token = _actor.set(user)
    try:
        yield
    finally:
        _actor.reset(token)


def actor_key(key: str) -> str:
    user = current_user()
    return f"user:{user['id']}:{key}" if user else key


class ActorMap(MutableMapping):
    """Private conversation processes/questions within a shared IP space."""
    def __init__(self):
        from easel.projects import ProjectMap
        self.items_by_ip = ProjectMap()

    def __getitem__(self, key):
        return self.items_by_ip[actor_key(key)]

    def __setitem__(self, key, value):
        self.items_by_ip[actor_key(key)] = value

    def __delitem__(self, key):
        del self.items_by_ip[actor_key(key)]

    def __iter__(self):
        user = current_user()
        prefix = f"user:{user['id']}:" if user else ''
        return iter([key[len(prefix):] for key in list(self.items_by_ip)
                     if key.startswith(prefix) and (prefix or not key.startswith('user:'))])

    def __len__(self):
        return sum(1 for _ in self)


class ActorSet(MutableSet):
    def __init__(self):
        self.mapping = ActorMap()

    def __contains__(self, key):
        return key in self.mapping

    def __iter__(self):
        return iter(self.mapping)

    def __len__(self):
        return len(self.mapping)

    def add(self, key):
        self.mapping[key] = True

    def discard(self, key):
        self.mapping.pop(key, None)
