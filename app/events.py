from __future__ import annotations
"""Publish/subscribe topic with fan-out. Stands in for SNS.

Publishers do not know who is listening. Each subscriber runs independently,
and one failing subscriber never blocks the others.
"""
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

from .db import Database

log = logging.getLogger("docflow.events")


class EventBus:
    def __init__(self):
        self._subs: dict[str, list[Callable[[dict], None]]] = defaultdict(list)

    def subscribe(self, topic: str, fn: Callable[[dict], None]) -> None:
        self._subs[topic].append(fn)

    def publish(self, topic: str, payload: dict) -> None:
        for fn in self._subs[topic]:
            try:
                fn(payload)
            except Exception:
                log.exception("subscriber %s failed for %s", getattr(fn, "__name__", fn), topic)


def make_notifier(db: Database) -> Callable[[dict], None]:
    """Notifier Lambda + SES stand-in: stores an email in the outbox table."""

    def notify(event: dict) -> None:
        if event["status"] == "DONE":
            subject = f"Your document {event['filename']} is ready"
            body = "Processing finished. Open DocFlow to see the extracted data."
        else:
            subject = f"Processing failed for {event['filename']}"
            body = f"Reason: {event.get('error', 'unknown')}"
        db.execute(
            "INSERT INTO emails (user, to_addr, subject, body, created_at) VALUES (?,?,?,?,?)",
            (event["user"], f"{event['user']}@example.com", subject, body, time.time()),
        )

    return notify


def make_audit_log(path: Path) -> Callable[[dict], None]:
    """A second, independent subscriber to show fan-out."""

    def audit(event: dict) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), **event}) + "\n")

    return audit
