"""Local process locks for execution ownership and workspaces."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator


class LocalLock:
    def __init__(self, database_path: str, key: str) -> None:
        directory = Path(database_path).resolve().parent / ".aec-locks"
        if directory.is_symlink():
            raise PermissionError("execution lock directory must not be a symlink")
        directory.mkdir(mode=0o700, exist_ok=True)
        filename = hashlib.sha256(key.encode()).hexdigest()
        self.descriptor = os.open(
            directory / filename,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )

    def try_acquire(self) -> bool:
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def close(self) -> None:
        os.close(self.descriptor)


@asynccontextmanager
async def held(database_path: str, key: str) -> AsyncIterator[None]:
    lock = LocalLock(database_path, key)
    try:
        while not lock.try_acquire():
            await asyncio.sleep(0.05)
        yield
    finally:
        lock.close()
