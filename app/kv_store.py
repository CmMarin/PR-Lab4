from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class Record:
    value: str
    timestamp: float


class AsyncKVStore:
    def __init__(self) -> None:
        self._data: Dict[str, Record] = {}
        self._lock = asyncio.Lock()

    async def set(self, key: str, value: str, timestamp: float) -> None:
        async with self._lock:
            current = self._data.get(key)
            if current is None or timestamp >= current.timestamp:
                self._data[key] = Record(value=value, timestamp=timestamp)

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            record = self._data.get(key)
            return record.value if record else None

    async def dump(self) -> Dict[str, Record]:
        async with self._lock:
            return {k: Record(value=v.value, timestamp=v.timestamp) for k, v in self._data.items()}
