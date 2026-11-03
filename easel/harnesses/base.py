"""Internal contract for agent harness integrations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol


class HarnessError(Exception):
    pass


Emit = Callable[[str, str | dict], None]
Ask = Callable[[list[dict]], Awaitable[dict[str, list[str]]]]
Bind = Callable[[str], None]
# The host owns authorization, filesystem validation and durable receipts.
Present = Callable[[list[dict], str], Awaitable[list[dict] | None]]
# Publication authority, exact-payload approval and receipts remain host-owned.
Publish = Callable[[dict, str], Awaitable[dict]]
# Read an existing scoped approval/operation; never create or execute one.
PublicationStatus = Callable[[str], Awaitable[dict]]
# Typed, read-only business queries; actor/IP scope and DTOs remain host-owned.
BusinessObjects = Callable[[dict, str], Awaitable[dict]]


@dataclass(frozen=True)
class Descriptor:
    id: str
    label: str
    protocol: str
    install_hint: str


@dataclass(frozen=True)
class RunRequest:
    prompt: str
    cwd: Path
    env: dict[str, str]
    native_id: str = ""
    model: str = ""
    sandbox: str = "workspace-write"
    timeout: int = 1800
    command: tuple[str, ...] = ()
    session_key: str = ""
    codex_auto_approve: bool = False
    autonomy_mode: str = "autonomous"
    present: Present | None = None
    publish: Publish | None = None
    publication_status: PublicationStatus | None = None
    business_objects: BusinessObjects | None = None


class Adapter(Protocol):
    descriptor: Descriptor

    async def run(self, request: RunRequest, emit: Emit, ask: Ask, bind: Bind) -> None: ...
