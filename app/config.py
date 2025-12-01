from __future__ import annotations

from dataclasses import dataclass
from typing import List
import os


@dataclass
class BaseConfig:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    role: str = os.getenv("ROLE", "leader")
    node_id: str = os.getenv("NODE_ID", "node")


@dataclass
class LeaderConfig(BaseConfig):
    follower_urls: List[str] | None = None
    write_quorum: int = int(os.getenv("WRITE_QUORUM", "3"))
    min_delay_ms: int = int(os.getenv("MIN_DELAY_MS", "0"))
    max_delay_ms: int = int(os.getenv("MAX_DELAY_MS", "1000"))
    request_timeout: float = float(os.getenv("REQUEST_TIMEOUT", "5"))

    def __post_init__(self) -> None:
        urls = os.getenv("FOLLOWER_URLS", "").strip()
        if urls:
            self.follower_urls = [u.strip().rstrip("/") for u in urls.split(",") if u.strip()]
        else:
            self.follower_urls = []
        if self.write_quorum < 1:
            raise ValueError("WRITE_QUORUM must be >= 1")
        if self.min_delay_ms < 0 or self.max_delay_ms < self.min_delay_ms:
            raise ValueError("Invalid delay bounds")


@dataclass
class FollowerConfig(BaseConfig):
    pass


def load_config() -> BaseConfig:
    role = os.getenv("ROLE", "leader").strip().lower()
    if role == "leader":
        return LeaderConfig(role="leader")
    if role == "follower":
        return FollowerConfig(role="follower")
    raise ValueError(f"Unsupported ROLE value: {role}")
