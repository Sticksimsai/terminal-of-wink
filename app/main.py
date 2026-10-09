"""Qubit Wink Terminal: FastAPI backend.

GET  /                 the terminal (static/index.html)
GET  /api/health       status, and whether the Claude voice is configured
POST /api/run          encode one string, or superpose two, and measure on Qiskit Aer
POST /api/chat         stream QPU-16's reply (server-sent events), then send it through qubits
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import quantum, voice

log = logging.getLogger("qubit-wink")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

STATIC = Path(__file__).resolve().parent.parent / "static"
TRUST_PROXY = os.environ.get("TRUST_PROXY", "1") == "1"
CHAT_PER_MIN = int(os.environ.get("CHAT_RATE_PER_MIN", "12"))
RUN_PER_MIN = int(os.environ.get("RUN_RATE_PER_MIN", "60"))
MAX_MSG_CHARS = 500
MAX_TURNS = 12

app = FastAPI(title="Qubit Wink Terminal", docs_url=None, redoc_url=None)


# ---------- small helpers ----------

class RateLimiter:
    """Sliding one-minute window per client IP, in memory (fine for one instance)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        if len(self.hits) > 10_000:  # keep memory bounded
            for k in [k for k, v in self.hits.items() if not v][:5_000]:
                del self.hits[k]
        return True


chat_limit = RateLimiter(CHAT_PER_MIN)
run_limit = RateLimiter(RUN_PER_MIN)


def client_ip(request: Request) -> str:
    if TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


# ---------- models ----------

class RunIn(BaseModel):
    a: str = Field(min_length=1)
    b: str | None = None
    shots: int = Field(1024, ge=1, le=quantum.MAX_SHOTS)
    noise: float = Field(0.0, ge=0.0, le=0.25)

    @field_validator("a", "b")
    @classmethod
    def fits(cls, v):
        if v is not None and len(v.encode("utf-8")) > quantum.MAX_RUN_BYTES:
            raise ValueError(f"max {quantum.MAX_RUN_BYTES} bytes ({quantum.MAX_RUN_BYTES * 8} qubits)")
        return v


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MSG_CHARS)


class ChatIn(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=MAX_TURNS)
    noise: float = Field(0.0, ge=0.0, le=0.25)


def normalize_turns(turns: list[Turn]) -> list[dict]:
    """Merge same-role neighbours and make the list start and end on a user turn."""
    out: list[dict] = []
    for t in turns:
        if out and out[-1]["role"] == t.role:
            out[-1]["content"] += "\n" + t.content
        else:
            out.append({"role": t.role, "content": t.content})
    while out and out[0]["role"] != "user":
        out.pop(0)
    if not out or out[-1]["role"] != "user":
        raise HTTPException(422, "the last message must be from the user")
    return out


# ---------- routes ----------

@app.get("/api/health")
async def health():
    return {"ok": True, "voice": "claude" if voice.enabled() else "offline",
            "model": voice.MODEL if voice.enabled() else None}


@app.post("/api/run")
async def run(body: RunIn, request: Request):
    if not run_limit.allow(client_ip(request)):
        raise HTTPException(429, "too many runs. wait a minute.")

    def work():
        p = quantum.plan(body.a, body.b)
        counts = quantum.run(p, body.shots, body.noise)
        return p, counts, quantum.draw(p), quantum.qiskit_code(p, body.shots)

    p, counts, drawing, code = await run_in_threadpool(work)
    targets = {p.a_bits, p.b_bits}
    return {
        "plan": p.to_dict(),
        "shots": body.shots,
        "noise": body.noise,
        "distinct": len(counts),
        "counts": [{"bits": k, "text": quantum.bits_to_text(k), "n": v, "hit": k in targets}
                   for k, v in counts[:64]],
        "drawing": drawing,
        "qiskit": code,
    }


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/chat")
async def chat(body: ChatIn, request: Request):
    if not chat_limit.allow(client_ip(request)):
        return JSONResponse({"detail": "the voice link is busy. wait a minute before the next message."}, 429)
    turns = normalize_turns(body.messages)

    async def events():
        text, offline = "", not voice.enabled()
        try:
            async for delta in voice.stream_reply(turns):
                text += delta
                yield sse("delta", {"text": delta})
        except Exception:  # API down, bad key, overload: keep the terminal talking
            log.exception("voice stream failed")
            if not text:
                offline = True
                text = voice.offline_reply(turns[-1]["content"])
                yield sse("delta", {"text": text})

        reply = voice.clean(text) or "..."

        def measure():
            measured, registers = quantum.measure_text(reply, body.noise)
            wink = quantum.measure_once(quantum.plan(";)", "8)"), body.noise)
            return measured, registers, wink

        measured, registers, wink = await run_in_threadpool(measure)
        sent = quantum.text_to_bits(reply)
        yield sse("done", {
            "reply": reply,
            "qubits": len(sent),
            "registers": registers,
            "sent_bits": sent,
            "measured_bits": measured,
            "measured_text": quantum.bits_to_text(measured),
            "flipped": sum(x != y for x, y in zip(sent, measured)),
            "wink": quantum.bits_to_text(wink),
            "wink_bits": wink,
            "offline": offline,
            "lore": bool(voice.LORE_Q.search(turns[-1]["content"])),
        })

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
