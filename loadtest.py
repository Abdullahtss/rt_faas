"""US-11 load probe: 200 concurrent ingest requests, report p95 processing_ms.

Run with the API already up:
  .\\.venv\\Scripts\\python loadtest.py
"""
from __future__ import annotations

import asyncio
import statistics
import time

import httpx

URL = "http://127.0.0.1:8000/api/v1/transactions"
HEADERS = {"Authorization": "Bearer rtfaas-dev-token"}
N = 200


async def one(client: httpx.AsyncClient, i: int) -> tuple:
    t0 = time.perf_counter()
    r = await client.post(
        URL,
        headers=HEADERS,
        json={
            "account_id": "ACC-%04d" % (1001 + (i % 20)),
            "amount": 3500 + i,
            "lat": 24.86,
            "lon": 67.00,
            "city": "Karachi",
            "merchant": "LoadTest",
        },
        timeout=10,
    )
    r.raise_for_status()
    wall = (time.perf_counter() - t0) * 1000
    return r.json().get("processing_ms", wall), wall


async def main() -> None:
    async with httpx.AsyncClient() as client:
        health = await client.get("http://127.0.0.1:8000/api/v1/health")
        health.raise_for_status()
        t0 = time.perf_counter()
        results = await asyncio.gather(*[one(client, i) for i in range(N)])
        elapsed = time.perf_counter() - t0
    proc = sorted(p for p, _ in results)
    wall = sorted(w for _, w in results)

    def pct(xs, p):
        return xs[min(len(xs) - 1, int(len(xs) * p))]

    print("requests   %d" % N)
    print("elapsed    %.2fs  (%.0f rps)" % (elapsed, N / elapsed))
    print("server ms  p50=%.1f  p95=%.1f  max=%.1f" % (pct(proc, 0.5), pct(proc, 0.95), proc[-1]))
    print("client ms  p50=%.1f  p95=%.1f  max=%.1f" % (pct(wall, 0.5), pct(wall, 0.95), wall[-1]))
    print("mean server %.1f" % statistics.mean(proc))
    ok = pct(wall, 0.95) <= 1000
    print("US-11 p95 client path <= 1000 ms: %s" % ("PASS" if ok else "FAIL"))


if __name__ == "__main__":
    asyncio.run(main())
