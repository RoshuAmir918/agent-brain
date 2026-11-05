"""One writer at a time per vault, across processes: the after-session writer, the nightly and the agent's tools."""
from __future__ import annotations

import fcntl
import hashlib
from contextlib import contextmanager
from pathlib import Path

from .config import Config


@contextmanager
def vault_lock(config: Config):
    """Held while a file the engine shares with other writers is read and rewritten. The lock file lives under
    ~/.cache/brain, never in the vault (so it is never committed) and never under TMPDIR (which differs between a
    launchd job and a shell, and would give two writers two locks). Not reentrant:
    nested calls open independent flock handles and can deadlock. Task mutation
    helpers stay lock-free; their CLI or enclosing engine operation owns it."""
    key = hashlib.sha256(str(config.vault_path.resolve()).encode("utf-8")).hexdigest()[:16]
    folder = Path.home() / ".cache" / "brain"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"vault-{key}.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def repo_lock(config: Config):
    """Held while git stages, commits, pulls or pushes the vault's repository. Separate from the vault lock: git only
    collides with git, and a push should not hold up every task write while it waits on the network. Not reentrant."""
    key = hashlib.sha256(str(config.repo.resolve()).encode("utf-8")).hexdigest()[:16]
    folder = Path.home() / ".cache" / "brain"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"repo-{key}.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
