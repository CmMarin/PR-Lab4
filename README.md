# Leader-Follower Key-Value Store

A minimal FastAPI-based key-value store demonstrating single-leader replication with semi-synchronous writes, dockerized deployment, automated integration tests, and a performance analysis harness.

## Features
- Leader-only writes with follower replication via async HTTP.
- Configurable write quorum, randomized per-follower replication delays, and concurrent request handling.
- Quorum counts follower confirmations only; leader waits for the configured number of follower ACKs before confirming a write.
- Runtime admin endpoints to adjust quorum and inspect replica state.
- Docker Compose topology with one leader and five followers.
- Pytest integration tests plus a workload script that sweeps quorum sizes, records latencies, and validates replica parity.

## Requirements
- Python 3.12+
- pip
- Docker + Docker Compose (for full multi-container deployment)

> **Note:** Docker commands are included for completeness, but they were not executed in this environment because Docker isn’t installed here.

## Install dependencies
```powershell
C:/Users/marin/AppData/Local/Programs/Python/Python312/python.exe -m pip install -r requirements.txt
```

## Run the API locally (leader-only)
```powershell
set ROLE=leader
set NODE_ID=leader-local
set FOLLOWER_URLS=
set WRITE_QUORUM=1
C:/Users/marin/AppData/Local/Programs/Python/Python312/python.exe -m uvicorn app.main:app --reload
```
Then, interact via `PUT /kv/{key}` and `GET /kv/{key}`.

## Docker Compose deployment
1. Build and start the cluster (one leader + five followers):
   ```powershell
   docker compose up --build
   ```
2. The leader listens on `http://localhost:8000`. Followers are reachable inside the compose network via `http://followerN:8000`.
3. To tear down:
   ```powershell
   docker compose down -v
   ```

## Admin endpoints
- `POST /admin/quorum {"write_quorum": int}` – adjust runtime quorum in follower-ACK units (leader only).
- `GET /admin/replica-status` – returns leader data snapshot plus each follower’s `/dump` output.

## Tests
Run the integration tests:
```powershell
C:/Users/marin/AppData/Local/Programs/Python/Python312/python.exe -m pytest
```

## Performance analysis
The script `scripts/run_workload.py` drives ~100 writes concurrently for quorum values 1–5, plots average latency, and checks data parity via the admin endpoint.

By default the script spins up a local leader + 5 follower cluster (using the current Python interpreter) so you can run it on a machine without Docker:

```powershell
C:/Users/marin/AppData/Local/Programs/Python/Python312/python.exe scripts/run_workload.py
```

Set `SPAWN_CLUSTER=0` if you want to reuse an already-running deployment instead.
Outputs:
- `reports/quorum_vs_latency.png`
- `reports/perf_summary.json`

## Expected observations
- Higher write quorum values increase average latency because the leader must synchronously wait for more follower acknowledgements before returning to the client.
- After the workload completes, the replica status report should show that each follower’s data matches the leader. If a follower lags or errors, the summary file captures mismatched keys.

## Project layout
```
app/                FastAPI application
  config.py        Env-driven settings loader
  kv_store.py      Async in-memory store abstraction
  main.py          API entrypoint
scripts/run_workload.py  Performance harness
tests/test_integration.py  Pytest integration tests
Dockerfile + docker-compose.yml  Containerized deployment
```
