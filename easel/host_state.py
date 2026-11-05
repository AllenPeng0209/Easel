"""Host authority is stored outside the native agent's writable workspace."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import sys


def publication_state_directory(repository: Path, project: str) -> Path:
    if not isinstance(project, str) or not project or any(character not in
            'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for character in project):
        raise ValueError('无效 IP 标识')
    repository = Path(repository).resolve()
    if sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support' / 'Easel' / 'host-state'
    elif os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'Easel' / 'host-state'
    else:
        base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'easel' / 'host-state'
    base = base.resolve()
    if base == repository or repository in base.parents:
        raise ValueError('发布批准状态必须保存在 Agent 可写工作区之外')
    identity = hashlib.sha256(str(repository).encode()).hexdigest()[:24]
    return base / identity / project / 'publications'


def generation_state_directory(repository: Path, project: str) -> Path:
    """Separate video admission authority, outside every native workspace."""
    return publication_state_directory(repository, project).parent / 'video-generations'
