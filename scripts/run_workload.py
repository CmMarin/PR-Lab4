from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from statistics import mean
from typing import List
import subprocess
import sys
from contextlib import suppress

import httpx
import matplotlib.pyplot as plt

REPORT_DIR = Path(os.getenv("REPORT_DIR", "reports"))
REPORT_DIR.mkdir(parents=True, exist_ok=True)
ROOT_DIR = Path(__file__).resolve().parents[1]

DEFAULT_FOLLOWERS = "http://127.0.0.1:8001,http://127.0.0.1:8002,http://127.0.0.1:8003,http://127.0.0.1:8004,http://127.0.0.1:8005"

LEADER_URL = os.getenv("LEADER_URL", "http://127.0.0.1:8000").rstrip("/")
FOLLOWER_URLS = [
    u.strip().rstrip("/") for u in os.getenv("FOLLOWER_URLS", DEFAULT_FOLLOWERS).split(",") if u.strip()
]
KEY_COUNT = int(os.getenv("KEY_COUNT", "10"))
TOTAL_WRITES = int(os.getenv("TOTAL_WRITES", "100"))
CONCURRENCY = int(os.getenv("WRITE_CONCURRENCY", "10"))
QUORUM_VALUES = [int(v) for v in os.getenv("QUORUM_VALUES", "1,2,3,4,5").split(",") if v.strip()]
SPAWN_CLUSTER = os.getenv("SPAWN_CLUSTER", "1").lower() not in {"0", "false", "no"}

FOLLOWER_PORTS = [8001, 8002, 8003, 8004, 8005]


def _spawn_process(env_overrides: dict) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(env_overrides)
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        env_overrides.get("HOST", "127.0.0.1"),
        "--port",
        str(env_overrides["PORT"]),
    ]
    return subprocess.Popen(
        cmd,
        cwd=ROOT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def start_cluster() -> List[subprocess.Popen]:
    processes: List[subprocess.Popen] = []
    follower_urls = []
    for idx, port in enumerate(FOLLOWER_PORTS, start=1):
        proc = _spawn_process(
            {
                "ROLE": "follower",
                "NODE_ID": f"follower{idx}",
                "PORT": str(port),
                "HOST": "127.0.0.1",
            }
        )
        processes.append(proc)
        follower_urls.append(f"http://127.0.0.1:{port}")

    leader_env = {
        "ROLE": "leader",
        "NODE_ID": "leader",
        "PORT": "8000",
        "HOST": "127.0.0.1",
        "FOLLOWER_URLS": ",".join(follower_urls),
    }
    if "MIN_DELAY_MS" in os.environ:
        leader_env["MIN_DELAY_MS"] = os.environ["MIN_DELAY_MS"]
    if "MAX_DELAY_MS" in os.environ:
        leader_env["MAX_DELAY_MS"] = os.environ["MAX_DELAY_MS"]
    if "REQUEST_TIMEOUT" in os.environ:
        leader_env["REQUEST_TIMEOUT"] = os.environ["REQUEST_TIMEOUT"]

    processes.append(_spawn_process(leader_env))
    return processes


def stop_cluster(processes: List[subprocess.Popen]) -> None:
    for proc in processes:
        with suppress(Exception):
            proc.terminate()
    for proc in processes:
        with suppress(Exception):
            proc.wait(timeout=5)
    for proc in processes:
        if proc.poll() is None:
            with suppress(Exception):
                proc.kill()


async def wait_for_cluster_ready(timeout: float = 20.0) -> None:
    targets = [LEADER_URL] + FOLLOWER_URLS
    deadline = time.time() + timeout
    pending = targets.copy()
    async with httpx.AsyncClient(timeout=2) as client:
        while pending and time.time() < deadline:
            still_pending = []
            for url in pending:
                try:
                    response = await client.get(f"{url}/health")
                    if response.status_code == 200:
                        continue
                except Exception:
                    pass
                still_pending.append(url)
            pending = still_pending
            if pending:
                await asyncio.sleep(0.5)
        if pending:
            raise RuntimeError(f"Services not ready: {pending}")


async def set_write_quorum(client: httpx.AsyncClient, quorum: int) -> None:
    response = await client.post(f"{LEADER_URL}/admin/quorum", json={"write_quorum": quorum})
    response.raise_for_status()


async def run_writes_for_quorum(client: httpx.AsyncClient, quorum: int) -> float:
    keys = [f"key-{i}" for i in range(KEY_COUNT)]
    latencies: List[float] = []
    semaphore = asyncio.Semaphore(CONCURRENCY)
    total_ops = TOTAL_WRITES

    async def write_op(idx: int) -> None:
        key = keys[idx % len(keys)]
        value = f"value-{quorum}-{idx}"
        payload = {"value": value}
        async with semaphore:
            start = time.perf_counter()
            response = await client.put(f"{LEADER_URL}/kv/{key}", json=payload)
            response.raise_for_status()
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

    await asyncio.gather(*(asyncio.create_task(write_op(i)) for i in range(total_ops)))
    return mean(latencies)


async def fetch_replica_status(client: httpx.AsyncClient) -> dict:
    response = await client.get(f"{LEADER_URL}/admin/replica-status")
    response.raise_for_status()
    return response.json()


def analyze_parity(status_payload: dict) -> List[dict]:
    leader = status_payload.get("leader", {})
    followers = status_payload.get("followers", {})
    results: List[dict] = []
    for url, payload in followers.items():
        if "error" in payload:
            results.append({"url": url, "status": "error", "detail": payload["error"]})
        else:
            mismatch_keys = [k for k in leader.keys() if payload.get(k) != leader.get(k)]
            extra_keys = [k for k in payload.keys() if k not in leader]
            if not mismatch_keys and not extra_keys:
                results.append({"url": url, "status": "match"})
            else:
                results.append(
                    {
                        "url": url,
                        "status": "mismatch",
                        "mismatch_keys": mismatch_keys,
                        "extra_keys": extra_keys,
                    }
                )
    return results


def plot_results(results: List[dict]) -> Path:
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot([r["quorum"] for r in results], [r["avg_latency_ms"] for r in results], marker="o")
    ax.set_xlabel("Write Quorum (acks)")
    ax.set_ylabel("Average write latency (ms)")
    ax.set_title("Write quorum vs latency")
    ax.grid(True, linestyle="--", alpha=0.5)
    output_path = REPORT_DIR / "quorum_vs_latency.png"
    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)
    return output_path


async def run_analysis() -> None:
    await wait_for_cluster_ready()
    results = []
    async with httpx.AsyncClient(timeout=15) as client:
        for quorum in QUORUM_VALUES:
            await set_write_quorum(client, quorum)
            avg_latency = await run_writes_for_quorum(client, quorum)
            results.append({"quorum": quorum, "avg_latency_ms": avg_latency})
            print(f"Quorum {quorum}: avg latency {avg_latency:.2f} ms")

        replica_status = await fetch_replica_status(client)

    parity = analyze_parity(replica_status)
    plot_path = plot_results(results)

    summary = {
        "leader_url": LEADER_URL,
        "results": results,
        "parity": parity,
        "plot": str(plot_path),
    }

    summary_path = REPORT_DIR / "perf_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"Summary written to {summary_path}")
    print(f"Plot saved to {plot_path}")


def main() -> None:
    processes: List[subprocess.Popen] = []
    try:
        if SPAWN_CLUSTER:
            processes = start_cluster()
        asyncio.run(run_analysis())
    finally:
        if processes:
            stop_cluster(processes)


if __name__ == "__main__":
    main()
