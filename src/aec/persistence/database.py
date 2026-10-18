"""Short SQLite transactions shared by broker operations."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .migrations import migrate


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self.connect() as connection:
            migrate(connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
