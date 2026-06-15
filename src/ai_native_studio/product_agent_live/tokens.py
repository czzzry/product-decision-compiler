"""Encrypted local storage for OAuth installation state and app tokens."""

from __future__ import annotations

import base64
import sqlite3
import time
from pathlib import Path
from threading import Lock

from cryptography.fernet import Fernet

from .models import StoredInstallation


def _normalize_fernet_key(key: str) -> bytes:
    raw = key.encode("ascii")
    try:
        Fernet(raw)
        return raw
    except ValueError:
        padded = base64.urlsafe_b64encode(raw.ljust(32, b"0")[:32])
        Fernet(padded)
        return padded


class OAuthStateStore:
    def __init__(self, connection: sqlite3.Connection, lock: Lock) -> None:
        self._connection = connection
        self._lock = lock

    def create(self, state: str, created_at_ms: int | None = None) -> None:
        timestamp = created_at_ms if created_at_ms is not None else int(time.time() * 1000)
        with self._lock:
            self._connection.execute(
                "INSERT INTO oauth_states(state, created_at_ms) VALUES (?, ?)",
                (state, timestamp),
            )
            self._connection.commit()

    def pop(self, state: str, max_age_ms: int, now_ms: int | None = None) -> bool:
        current = now_ms if now_ms is not None else int(time.time() * 1000)
        with self._lock:
            row = self._connection.execute(
                "SELECT created_at_ms FROM oauth_states WHERE state = ?",
                (state,),
            ).fetchone()
            self._connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            self._connection.commit()
        return bool(row) and current - int(row[0]) <= max_age_ms


class InstallationStore:
    installation_key = "default"

    def __init__(self, database_path: str | Path, encryption_key: str) -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._lock = Lock()
        self._fernet = Fernet(_normalize_fernet_key(encryption_key))
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                created_at_ms INTEGER NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS installations (
                installation_key TEXT PRIMARY KEY,
                access_token BLOB NOT NULL,
                refresh_token BLOB NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                scope TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._connection.commit()
        self.oauth_states = OAuthStateStore(self._connection, self._lock)

    def save_installation(self, installation: StoredInstallation) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO installations(
                    installation_key,
                    access_token,
                    refresh_token,
                    expires_at_ms,
                    scope
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(installation_key) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at_ms = excluded.expires_at_ms,
                    scope = excluded.scope
                """,
                (
                    self.installation_key,
                    self._fernet.encrypt(installation.access_token.encode("utf-8")),
                    self._fernet.encrypt(installation.refresh_token.encode("utf-8")),
                    installation.expires_at_ms,
                    " ".join(installation.scope),
                ),
            )
            self._connection.commit()

    def load_installation(self) -> StoredInstallation | None:
        row = self._connection.execute(
            """
            SELECT access_token, refresh_token, expires_at_ms, scope
            FROM installations
            WHERE installation_key = ?
            """,
            (self.installation_key,),
        ).fetchone()
        if not row:
            return None
        return StoredInstallation(
            access_token=self._fernet.decrypt(row[0]).decode("utf-8"),
            refresh_token=self._fernet.decrypt(row[1]).decode("utf-8"),
            expires_at_ms=int(row[2]),
            scope=tuple(str(row[3]).split()),
        )

    def set_metadata(self, key: str, value: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            self._connection.commit()

    def get_metadata(self, key: str) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM runtime_metadata WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else str(row[0])

    def close(self) -> None:
        self._connection.close()
