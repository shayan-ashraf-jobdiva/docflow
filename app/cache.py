from __future__ import annotations
"""In-process TTL cache. Stands in for Redis (ElastiCache)."""
import threading
import time


class TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < time.time():
                del self._data[key]
                return None
            return value

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = (time.time() + self.ttl, value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)
