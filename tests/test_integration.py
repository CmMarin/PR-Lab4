from __future__ import annotations

import asyncio
import httpx
import pytest

from app.kv_store import AsyncKVStore
from app import main


@pytest.mark.asyncio
async def test_leader_write_with_quorum(monkeypatch):
    print("[test_leader_write_with_quorum] configuring leader with five followers and quorum=3")
    main.config.role = "leader"
    main.config.follower_urls = [
        "http://follower1:8000",
        "http://follower2:8000",
        "http://follower3:8000",
        "http://follower4:8000",
        "http://follower5:8000",
    ]
    main.config.write_quorum = 3
    main.config.min_delay_ms = 0
    main.config.max_delay_ms = 0

    print("[test_leader_write_with_quorum] resetting in-memory store")
    main.store = AsyncKVStore()

    await main.startup_event()

    calls = []

    async def fake_post(url: str, json: dict, timeout: float):
        print(f"[test_leader_write_with_quorum] attempting replication to {url}")
        calls.append(url)
        if any(f"follower{n}" in url for n in (1, 2, 3)):
            return httpx.Response(200, request=httpx.Request("POST", url))
        raise httpx.HTTPError("simulated failure")

    monkeypatch.setattr(main.app.state.http_client, "post", fake_post)

    async with httpx.AsyncClient(app=main.app, base_url="http://test") as client:
        print("[test_leader_write_with_quorum] issuing client PUT /kv/test")
        resp = await client.put("/kv/test", json={"value": "123"})

    await main.shutdown_event()

    print(f"[test_leader_write_with_quorum] response status={resp.status_code}, body={resp.json()}")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["follower_acks"] == 3
    assert payload["quorum_met"] is True
    stored = await main.store.get("test")
    print(f"[test_leader_write_with_quorum] stored value for test={stored}")
    assert stored == "123"
    assert len(calls) >= 3

    print("[test_leader_write_with_quorum] verified quorum enforcement and replication fan-out")


@pytest.mark.asyncio
async def test_follower_replication_endpoint():
    print("[test_follower_replication_endpoint] configuring follower role")
    main.config.role = "follower"
    main.store = AsyncKVStore()

    async with httpx.AsyncClient(app=main.app, base_url="http://test") as client:
        print("[test_follower_replication_endpoint] posting replication payload to follower")
        resp = await client.post(
            "/replicate",
            json={"key": "alpha", "value": "beta", "timestamp": 123.0},
        )
        print(f"[test_follower_replication_endpoint] response status={resp.status_code}, body={resp.json()}")
        assert resp.status_code == 200

    stored_value = await main.store.get("alpha")
    print(f"[test_follower_replication_endpoint] stored value for alpha={stored_value}")
    assert stored_value == "beta"

    print("[test_follower_replication_endpoint] follower applied replicated write successfully")
