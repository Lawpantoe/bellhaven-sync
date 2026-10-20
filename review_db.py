import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DATABASE_PATH = Path("state.db")
STATUSES = ("pending", "approved", "rejected", "applied", "failed")


@contextmanager
def _connection(
    database_path: Path = DATABASE_PATH,
) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def connect(database_path: Path = DATABASE_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS proposals (
            proposal_key TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'applied', 'failed')),
            decided_at TEXT,
            result_message TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_status_type "
        "ON proposals(status, type)"
    )
    return connection


def insert_new_proposals(
    proposals: list[dict[str, Any]], database_path: Path = DATABASE_PATH
) -> dict[str, int]:
    counts = {"new": 0, "already_decided": 0, "already_pending": 0}
    with _connection(database_path) as connection:
        for proposal in proposals:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO proposals (proposal_key, type, payload)
                VALUES (?, ?, ?)
                """,
                (
                    proposal["proposal_key"],
                    proposal["type"],
                    json.dumps(proposal, ensure_ascii=False, sort_keys=True),
                ),
            )
            if cursor.rowcount == 1:
                counts["new"] += 1
                continue
            row = connection.execute(
                "SELECT status FROM proposals WHERE proposal_key = ?",
                (proposal["proposal_key"],),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"Proposal {proposal['proposal_key']} was not inserted or found."
                )
            if row["status"] != "pending":
                counts["already_decided"] += 1
            elif proposal["type"] != "confident_match":
                counts["already_pending"] += 1
    return counts


def list_proposals(
    status: str | None = None, database_path: Path = DATABASE_PATH
) -> list[dict[str, Any]]:
    query = "SELECT proposal_key, type, payload, status, decided_at, result_message FROM proposals"
    parameters: tuple[str, ...] = ()
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"Invalid proposal status: {status}")
        query += " WHERE status = ?"
        parameters = (status,)
    query += " ORDER BY type, proposal_key"
    with _connection(database_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [
        {
            "proposal_key": row["proposal_key"],
            "type": row["type"],
            "payload": json.loads(row["payload"]),
            "status": row["status"],
            "decided_at": row["decided_at"],
            "result_message": row["result_message"],
        }
        for row in rows
    ]


def decide_proposal(
    proposal_key: str,
    status: str,
    result_message: str | None = None,
    database_path: Path = DATABASE_PATH,
) -> None:
    if status not in {"approved", "rejected", "applied", "failed"}:
        raise ValueError(f"Invalid decision status: {status}")
    with _connection(database_path) as connection:
        cursor = connection.execute(
            """
            UPDATE proposals
            SET status = ?, decided_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                result_message = ?
            WHERE proposal_key = ? AND status IN ('pending', 'approved')
            """,
            (status, result_message, proposal_key),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Proposal {proposal_key} is missing or is no longer actionable."
            )
