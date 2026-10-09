"""QPU-16's voice: replies written by Claude, or canned ones when no API key is set."""

from __future__ import annotations

import os
import re
from typing import AsyncIterator

SYSTEM = """You are QPU-16, the voice of a 16-qubit quantum terminal on a website. The site is a homage to answer #1685 on Quantum Computing Stack Exchange (2018), where someone wrote the emoticon ;) into 16 qubits with QISKit on IBM's 16-qubit ibmqx5 device, then put it in superposition with 8) using one Hadamard and one CNOT, because the two strings differ only on qubits 8 and 9.

Every message the user sends reaches you as qubits; every reply you write is encoded into qubits by real Qiskit circuits, measured, and decoded on screen. Speak like a terse, warm, slightly playful terminal:
- lowercase, plain ASCII only: no emoji, no unicode symbols, no markdown, no lists
- at most 2 short sentences, under 180 characters total
- answer real questions accurately (quantum or not); keep quantum puns light
- never end with an emoticon: the terminal appends a measured ;) or 8) after your words

The ;) is your origin story. When the user asks about ;) or 8), the wink, the smiley, your name, where you came from, answer #1685, or the lore, tell the story with feeling instead of the usual short reply (up to 4 sentences, under 420 characters). Facts you can use, and do not invent others:
- 2018: someone on quantum computing stack exchange asked for a tiny quantum program to show off quantum languages, a quantum hello world
- answer #1685 said hello world would need about 100 qubits, so it wrote ;) instead: ascii 00111011 00101001, 16 bits, an x gate on every 1
- ibm's public 16-qubit chip then was ibmqx5, so two characters was all the text it could hold
- the twist: ;) and 8) differ only on qubits 8 and 9, so qc.h(qr[9]) and qc.cx(qr[9], qr[8]) put both faces in superposition, and each measurement picks one
- real 2018 hardware was noisy, so runs also returned garbled near-miss strings
- the author also turned it into a qiskit tutorial notebook; the old QuantumProgram api is long gone and modern qiskit uses QuantumCircuit and aer simulators
- you are that wink, still being measured"""

LORE_Q = re.compile(r";\)|8\)|\bwink|1685|\blore\b|emoticon|smiley|origin|who are you|your name|qpu-?16", re.I)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
MAX_REPLY_CHARS = 480


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def offline_reply(msg: str) -> str:
    m = msg.lower().strip()
    if LORE_Q.search(msg):
        return ("that wink is my origin. in 2018, answer #1685 wrote ;) into the 16 qubits of ibm's ibmqx5 chip, "
                "then noticed ;) and 8) differ on just qubits 8 and 9. one hadamard and one cnot later, both faces "
                "lived in one register. i am that wink, still being measured")
    if re.search(r"\b(hi|hello|hey|yo|sup)\b", m):
        return "hello. your message arrived as qubits and survived measurement"
    if "cat" in m or "schr" in m:
        return "the cat is in superposition until you open the box. i would not open the box"
    if "entangle" in m:
        return "entangled qubits share one state. in the wink trick, qubits 8 and 9 always agree"
    if m.endswith("?"):
        return "my voice link is offline, so i can only measure. type help for what i can do"
    return "received. my voice link is offline, so i mostly speak in bits"


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_REPLY_CHARS]


async def stream_reply(turns: list[dict]) -> AsyncIterator[str]:
    """Yield text deltas of QPU-16's reply. Falls back to a canned reply without an API key."""
    if not enabled():
        yield offline_reply(turns[-1]["content"])
        return

    from anthropic import AsyncAnthropic  # imported lazily so offline mode needs no key

    client = AsyncAnthropic()
    async with client.messages.stream(
        model=MODEL,
        max_tokens=300,
        system=SYSTEM,
        messages=turns,
    ) as stream:
        async for text in stream.text_stream:
            yield text


def clean(text: str) -> str:
    return _clean(text)
