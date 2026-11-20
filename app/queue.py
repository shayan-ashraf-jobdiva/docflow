from __future__ import annotations
"""Message queue with SQS semantics, backed by SQLite.

- send(): enqueue a message.
- receive(): hand out one visible message and hide it for `visibility_timeout`.
- delete(): acknowledge a message after successful processing.
- A message received `max_receives` times without being deleted moves to the
  dead-letter queue (SQS redrive policy).
"""
import json
import time
from dataclasses import dataclass
from typing import Callable

from .db import Database


@dataclass
class Message:
    id: int
    body: dict
    receive_count: int


class Queue:
    def __init__(self, db: Database, visibility_timeout: float, max_receives: int):
        self.db = db
        self.visibility_timeout = visibility_timeout
        self.max_receives = max_receives

    def send(self, body: dict) -> None:
        self.db.execute("INSERT INTO queue (body, visible_at) VALUES (?, 0)", (json.dumps(body),))

    def receive(self, on_dead: Callable[[dict], None] | None = None) -> Message | None:
        while True:
            now = time.time()
            dead_body = None
            with self.db.tx() as c:
                row = c.execute(
                    "SELECT * FROM queue WHERE state='ready' AND visible_at<=? ORDER BY id LIMIT 1", (now,)
                ).fetchone()
                if row is None:
                    return None
                if row["receive_count"] >= self.max_receives:
                    c.execute("UPDATE queue SET state='dlq' WHERE id=?", (row["id"],))
                    dead_body = json.loads(row["body"])
                else:
                    c.execute(
                        "UPDATE queue SET receive_count=receive_count+1, visible_at=? WHERE id=?",
                        (now + self.visibility_timeout, row["id"]),
                    )
                    return Message(row["id"], json.loads(row["body"]), row["receive_count"] + 1)
            if on_dead and dead_body is not None:
                on_dead(dead_body)

    def delete(self, message_id: int) -> None:
        self.db.execute("DELETE FROM queue WHERE id=?", (message_id,))

    def replay_dead_letters(self) -> int:
        return self.db.execute(
            "UPDATE queue SET state='ready', receive_count=0, visible_at=0 WHERE state='dlq'"
        )

    def stats(self) -> dict:
        now = time.time()
        r = self.db.query(
            "SELECT"
            " SUM(CASE WHEN state='ready' AND visible_at<=? THEN 1 ELSE 0 END) AS ready,"
            " SUM(CASE WHEN state='ready' AND visible_at>? THEN 1 ELSE 0 END) AS in_flight,"
            " SUM(CASE WHEN state='dlq' THEN 1 ELSE 0 END) AS dead_letter"
            " FROM queue",
            (now, now),
        )[0]
        return {"ready": r["ready"] or 0, "in_flight": r["in_flight"] or 0, "dead_letter": r["dead_letter"] or 0}
