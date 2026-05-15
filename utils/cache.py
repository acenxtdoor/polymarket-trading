import time


class TTLCache:
    def __init__(self, ttl: int):
        self._ttl = ttl
        self._store: dict = {}

    def get(self, key: str):
        entry = self._store.get(key)
        if entry and time.time() - entry["ts"] < self._ttl:
            return entry["value"]
        return None

    def set(self, key: str, value) -> None:
        self._store[key] = {"value": value, "ts": time.time()}

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
