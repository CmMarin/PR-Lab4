# Architecture Overview

## Components
- **Leader service (FastAPI)**: Accepts all client writes, stores them in local in-memory kv store, replicates to followers using concurrent async HTTP calls.
- **Follower service (FastAPI)**: Read-only for clients, but exposes internal `/replicate` endpoint so the leader can push updates. Clients can still read via `/kv/{key}` on any node.
- **Shared data layer**: Simple asyncio-aware in-memory store (`dict` + `asyncio.Lock`) shared by both roles via a reusable module.
- **Client/perf harness**: Python script that issues concurrent writes, sweeps `WRITE_QUORUM`, captures latency metrics, and validates leader/follower data parity.

## API Surface
### Leader (ROLE=leader)
- `GET /health`: readiness probe.
- `GET /kv/{key}`: returns `{ "key": str, "value": str | null }`.
- `PUT /kv/{key}`: body `{ "value": str }`. Writes locally, replicates to followers, waits for at least `WRITE_QUORUM` follower acknowledgements before reporting success. Returns `{ "key": str, "value": str, "quorum_met": bool }`.

### Follower (ROLE=follower)
- `GET /health`: readiness probe.
- `GET /kv/{key}`: same as leader for read scalability/testing.
- `POST /replicate`: body `{ "key": str, "value": str, "timestamp": float }`. Applies the write locally (overwriting previous value). Returns `{ "status": "ok" }`.

All responses are JSON; errors use standard HTTP status codes with JSON payloads.

## Replication Flow
1. Client sends `PUT /kv/{key}` to leader.
2. Leader writes to local store immediately (this counts as one acknowledgement).
3. Leader assembles follower URLs from `FOLLOWER_URLS` env var, and for each follower schedules an async replication coroutine that:
   - Waits a random delay `uniform(MIN_DELAY_MS, MAX_DELAY_MS)` to emulate network lag.
   - Issues `POST /replicate` to the follower with the write payload.
4. The leader awaits follower responses concurrently using `asyncio.as_completed`, counting confirmations until `WRITE_QUORUM` follower acknowledgements are reached or all requests finish.
5. If quorum is met, HTTP 200 is returned to the client; otherwise 503 with details about the number of successful replicas.

This implements **semi-synchronous replication** because the leader returns success only after receiving a configurable number of follower confirmations, but it does not require every follower to respond.

## Concurrency & Storage
- FastAPI + Uvicorn (async) ensures concurrent request handling for both roles.
- In-memory store implemented as `class AsyncKVStore` with methods `get`, `set`, `dump`, protected by an `asyncio.Lock` to serialize writes per instance.
- Replication tasks use `asyncio.create_task` so delays do not block other requests.

## Configuration (env vars)
| Variable | Role | Description |
| --- | --- | --- |
| `ROLE` | both | `"leader"` or `"follower"` to select API wiring |
| `HOST` | both | Bind host (default `0.0.0.0`) |
| `PORT` | both | Service port |
| `NODE_ID` | both | Human-friendly identifier for logs |
| `FOLLOWER_URLS` | leader | Comma-separated base URLs for follower HTTP endpoints (e.g., `http://follower1:8000,http://follower2:8000`) |
| `WRITE_QUORUM` | leader | Minimum acknowledgements (including the leader) required for a write success |
| `MIN_DELAY_MS`, `MAX_DELAY_MS` | leader | Bounds for random delay before each follower request |
| `REQUEST_TIMEOUT` | leader | HTTP timeout for replication calls |

Docker Compose will supply these env vars; defaults exist for local dev/testing.

## Docker Topology
- Single image built from repo.
- Compose spins up 1 leader container and 5 follower containers on the same network.
- Leader advertises follower URLs using service names (e.g., `http://follower1:8000`).
- Optionally, compose can expose leader port to host for manual testing.

## Testing & Tooling
- `pytest` integration test spins up leader + followers with `TestClient` + mocked follower endpoints to verify quorum and replication logic without Docker.
- Performance harness (`scripts/run_workload.py`):
  - Issues ~100 writes (10 concurrent) across 10 keys.
  - Iterates `WRITE_QUORUM` in `[1,5]` via env overrides / leader API parameter.
  - Measures per-write latency, aggregates averages, and plots `quorum vs. latency` using matplotlib.
  - After workload completes, fetches dumps from leader/followers and asserts equality, reporting discrepancies.

This design keeps the stack small (pure Python, FastAPI, httpx) while fulfilling concurrency, semi-sync replication, env-based configuration, Dockerization, testing, and performance analysis requirements.
