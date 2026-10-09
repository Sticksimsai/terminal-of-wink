# Qubit Wink Terminal

A black-and-white terminal that talks through qubits. A homage to
[Quantum Computing Stack Exchange answer #1685](https://quantumcomputing.stackexchange.com/a/1685),
which wrote `;)` into 16 qubits and then put it in superposition with `8)` using one Hadamard and one CNOT.

- **Real Qiskit.** Every circuit runs on Qiskit Aer's stabilizer simulator. The circuits only use X, H and CNOT,
  so it handles hundreds of qubits in milliseconds.
- **It talks.** Messages go to Claude as QPU-16. The reply streams in as bits, is written into qubits,
  measured, and decoded on screen. Every reply ends with a measured `;)` or `8)`. Ask about `;)` and it tells its origin story.
- **Noise.** `noise 0.03` adds readout errors to runs and to the chat, so replies come back garbled like 2018 hardware.

## Layout

```
app/main.py      FastAPI routes, rate limiting, server-sent events
app/quantum.py   text <-> qubits, superposition circuits, Aer runs, Qiskit code export
app/voice.py     QPU-16's personality and lore, Claude streaming, offline fallback
static/index.html  the terminal
tests/           pytest suite
```

API: `GET /api/health`, `POST /api/run {a, b?, shots, noise}`, `POST /api/chat {messages, noise}` (event stream).

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # put your ANTHROPIC_API_KEY in it
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. Without an API key it still runs, with canned replies.

Tests: `pip install pytest && pytest -q`

With Docker:

```bash
docker build -t qubit-wink .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... qubit-wink
```

## Deploy and connect your domain

Any host that runs a Docker container works. Two easy options:

**Render**
1. Push this folder to a GitHub repo.
2. In Render: New > Blueprint, pick the repo. `render.yaml` sets everything up.
3. Add `ANTHROPIC_API_KEY` when it asks.
4. Settings > Custom Domains > add `yourdomain.com` and `www.yourdomain.com`. Render shows the DNS records to create.

**Fly.io**
```bash
fly launch --no-deploy          # detects the Dockerfile; set internal port 8000
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly deploy
fly certs add yourdomain.com
```

**DNS at your registrar** (use the exact values your host shows you):

| Record | Name  | Points to |
|--------|-------|-----------|
| CNAME  | `www` | your app's host name, e.g. `qubit-wink.onrender.com` |
| A / ALIAS | `@` (root) | the IP or target your host gives for the root domain |

HTTPS certificates are issued automatically once DNS resolves, usually within minutes, sometimes up to an hour.
If you use Cloudflare in front, set SSL mode to "Full" so the certificate chain works.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Turns on the Claude voice. Without it, replies are canned. |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` | Model for QPU-16's replies. |
| `CHAT_RATE_PER_MIN` | `12` | Chat messages per visitor IP per minute. |
| `RUN_RATE_PER_MIN` | `60` | Circuit runs per visitor IP per minute. |
| `TRUST_PROXY` | `1` | Read the visitor IP from `X-Forwarded-For`. Set `0` if nothing sits in front of the app. |

## Notes

- The chat uses your Anthropic API key, so public traffic costs you money. The rate limits above cap each visitor;
  also set a monthly spend limit in the Anthropic console.
- Rate limits are kept in memory, so run a single instance (the default on both hosts).
- `/api/run` strings are capped at 32 bytes (256 qubits); chat replies at 480 characters, measured as 128-qubit registers.
