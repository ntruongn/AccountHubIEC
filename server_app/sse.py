"""Thread-safe Server-Sent Events (SSE) broker for live dashboard push updates."""
from __future__ import annotations

import json
import queue
import threading
from typing import Any


class EventBroker:
    def __init__(self, max_queue_size: int = 64):
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[tuple[str, str]]] = set()
        self._max_queue_size = max_queue_size

    def subscribe(self) -> queue.Queue[tuple[str, str]]:
        q: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=self._max_queue_size)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[tuple[str, str]]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait((event, payload))
            except queue.Full:
                # Drop oldest event so slow clients stay fresh
                try:
                    q.get_nowait()
                    q.put_nowait((event, payload))
                except queue.Empty:
                    pass
