"""SQLite-backed webhook receipt ledger."""

import sqlite3
from enum import StrEnum
from pathlib import Path
from threading import Lock


class ReceiptResult(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


class WebhookReceiptStore:
    """Persist webhook IDs so duplicate and conflicting replays are rejected."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._lock = Lock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_receipts (
                webhook_id TEXT PRIMARY KEY,
                payload_sha256 TEXT NOT NULL,
                received_at_ms INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved'
            )
            """
        )
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(webhook_receipts)")
        }
        if "status" not in columns:
            self._connection.execute(
                "ALTER TABLE webhook_receipts ADD COLUMN status TEXT NOT NULL DEFAULT 'reserved'"
            )
        self._connection.commit()

    def reserve(
        self,
        webhook_id: str,
        payload_sha256: str,
        received_at_ms: int,
        *,
        stale_after_ms: int = 5 * 60 * 1000,
    ) -> ReceiptResult:
        with self._lock:
            try:
                self._connection.execute(
                    """
                    INSERT INTO webhook_receipts(
                        webhook_id,
                        payload_sha256,
                        received_at_ms,
                        status
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (webhook_id, payload_sha256, received_at_ms, "reserved"),
                )
                self._connection.commit()
                return ReceiptResult.NEW
            except sqlite3.IntegrityError:
                row = self._connection.execute(
                    """
                    SELECT payload_sha256, received_at_ms, status
                    FROM webhook_receipts
                    WHERE webhook_id = ?
                    """,
                    (webhook_id,),
                ).fetchone()

            if row and row[0] == payload_sha256:
                status = str(row[2] or "reserved")
                age_ms = received_at_ms - int(row[1])
                if status == "completed" or age_ms <= stale_after_ms:
                    return ReceiptResult.DUPLICATE
                self._connection.execute(
                    """
                    UPDATE webhook_receipts
                    SET payload_sha256 = ?, received_at_ms = ?, status = ?
                    WHERE webhook_id = ?
                    """,
                    (payload_sha256, received_at_ms, "reserved", webhook_id),
                )
                self._connection.commit()
                return ReceiptResult.NEW
        return ReceiptResult.CONFLICT

    def complete(self, webhook_id: str, payload_sha256: str) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_sha256 FROM webhook_receipts WHERE webhook_id = ?",
                (webhook_id,),
            ).fetchone()
            if row and row[0] == payload_sha256:
                self._connection.execute(
                    "UPDATE webhook_receipts SET status = ? WHERE webhook_id = ?",
                    ("completed", webhook_id),
                )
                self._connection.commit()

    def release(self, webhook_id: str, payload_sha256: str) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_sha256 FROM webhook_receipts WHERE webhook_id = ?",
                (webhook_id,),
            ).fetchone()
            if row and row[0] == payload_sha256:
                self._connection.execute(
                    "DELETE FROM webhook_receipts WHERE webhook_id = ?",
                    (webhook_id,),
                )
                self._connection.commit()

    def close(self) -> None:
        self._connection.close()
