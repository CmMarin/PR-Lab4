from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Dict

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel

from .config import LeaderConfig, load_config
from .kv_store import AsyncKVStore

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("kvstore")

store = AsyncKVStore()
config = load_config()
app = FastAPI(title="Leader-Follower KV Store", version="0.1.0")


class WriteRequest(BaseModel):
    value: str


class ReplicateRequest(BaseModel):
    key: str
    value: str
    timestamp: float


class QuorumUpdate(BaseModel):
    write_quorum: int


@app.on_event("startup")
async def startup_event() -> None:
    app.state.http_client = httpx.AsyncClient()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    client: httpx.AsyncClient = app.state.http_client
    await client.aclose()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "role": config.role, "node_id": config.node_id}


@app.get("/kv/{key}")
async def get_value(key: str) -> dict:
    value = await store.get(key)
    return {"key": key, "value": value}


@app.get("/dump")
async def dump_store() -> dict:
    return await _serialize_store()


async def _serialize_store() -> Dict[str, str]:
    data = await store.dump()
    return {k: v.value for k, v in data.items()}


async def replicate_to_followers(
    cfg: LeaderConfig,
    key: str,
    value: str,
    timestamp: float,
    client: httpx.AsyncClient,
) -> int:
    if not cfg.follower_urls:
        return 0

    async def replicate(url: str) -> bool:
        delay_ms = random.uniform(cfg.min_delay_ms, cfg.max_delay_ms)
        await asyncio.sleep(delay_ms / 1000.0)
        try:
            response = await client.post(
                f"{url}/replicate",
                json={"key": key, "value": value, "timestamp": timestamp},
                timeout=cfg.request_timeout,
            )
            response.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Replication to %s failed: %s", url, exc)
            return False

    tasks = [asyncio.create_task(replicate(url)) for url in cfg.follower_urls]
    follower_acks = 0

    try:
        for future in asyncio.as_completed(tasks):
            success = await future
            if success:
                follower_acks += 1
            if follower_acks >= cfg.write_quorum:
                break
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    return follower_acks


@app.put("/kv/{key}")
async def put_value(key: str, request: WriteRequest, client: httpx.AsyncClient = Depends(lambda: app.state.http_client)) -> dict:
    if config.role != "leader":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Writes allowed only on leader")

    if not isinstance(config, LeaderConfig):
        raise HTTPException(status_code=500, detail="Invalid leader configuration")

    timestamp = time.time()
    await store.set(key, request.value, timestamp)

    follower_acks = await replicate_to_followers(config, key, request.value, timestamp, client)
    quorum_met = follower_acks >= config.write_quorum

    if not quorum_met:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Write quorum not met",
                "follower_acks": follower_acks,
                "required": config.write_quorum,
            },
        )

    return {
        "key": key,
        "value": request.value,
        "follower_acks": follower_acks,
        "quorum_met": True,
    }


@app.post("/replicate")
async def replicate(request: ReplicateRequest) -> dict:
    if config.role != "follower":
        # Leader may also receive replication requests during tests; accept but log.
        logger.info("Replication endpoint called on leader; applying data")
    await store.set(request.key, request.value, request.timestamp)
    return {"status": "ok"}


@app.post("/admin/quorum")
async def update_quorum(payload: QuorumUpdate) -> dict:
    if config.role != "leader" or not isinstance(config, LeaderConfig):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Quorum updates allowed only on leader")
    if payload.write_quorum < 1:
        raise HTTPException(status_code=400, detail="write_quorum must be >= 1")
    config.write_quorum = payload.write_quorum
    return {"write_quorum": config.write_quorum}


@app.get("/admin/replica-status")
async def replica_status(client: httpx.AsyncClient = Depends(lambda: app.state.http_client)) -> dict:
    if config.role != "leader" or not isinstance(config, LeaderConfig):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Replica status available only on leader")

    leader_state = await _serialize_store()
    followers: Dict[str, Dict[str, str]] = {}

    async def fetch(url: str) -> None:
        try:
            response = await client.get(f"{url}/dump", timeout=config.request_timeout)
            response.raise_for_status()
            followers[url] = response.json()
        except Exception as exc:  # noqa: BLE001
            followers[url] = {"error": str(exc)}

    if config.follower_urls:
        await asyncio.gather(*(fetch(url) for url in config.follower_urls))

    return {"leader": leader_state, "followers": followers}
