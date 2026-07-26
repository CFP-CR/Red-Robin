"""Phase 1 spike stub daemon — minimal FastAPI app for HTTP latency measurement.

Run with: uv run python adapters/cobaltstrike/spike/stub_daemon.py

Used by adapters/cobaltstrike/spike/test_http_latency.cna to measure Sleep HTTP
round-trip latency without requiring the full Red Robin daemon.
"""

from __future__ import annotations

from fastapi import FastAPI
import uvicorn


app = FastAPI(title="Red Robin Spike Stub", version="0.0.1")


@app.get("/v1/ping")
async def ping() -> dict[str, bool]:
    return {"ok": True}


@app.post("/v1/events")
async def events() -> dict[str, bool]:
    # Accept-and-discard — this is a latency-only stub for the capture path.
    return {"accepted": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7777)
