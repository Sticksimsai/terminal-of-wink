"""Qubit text encoding, in the style of Quantum Computing Stack Exchange answer #1685.

Text is written into a register as UTF-8 bits with X gates (qubit 0 is the
rightmost bit, as Qiskit orders them). Two strings are put in superposition the
way the answer superposed ;) and 8): X on the bits they share, a Hadamard on
the highest differing qubit, and CNOTs copying that coin flip onto the other
differing qubits.

Every circuit here is Clifford (X, H, CX), so Aer's stabilizer simulator runs
hundreds of qubits in milliseconds instead of needing a 2^n statevector.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit_aer import AerSimulator

MAX_RUN_BYTES = 32          # /api/run strings: 256 qubits
MAX_SHOTS = 8192
MAX_DRAW_QUBITS = 32

_sim = AerSimulator(method="stabilizer")


def text_to_bits(text: str) -> str:
    return "".join(f"{b:08b}" for b in text.encode("utf-8"))


def bits_to_text(bits: str) -> str:
    data = int(bits, 2).to_bytes(len(bits) // 8, "big") if bits else b""
    text = data.decode("utf-8", "replace")
    return "".join("." if (ord(c) < 32 or ord(c) == 127) else c for c in text)


@dataclass
class Plan:
    """A circuit described as gate operations, plus the strings it encodes."""

    n: int
    a_text: str
    a_bits: str
    b_text: str | None = None
    b_bits: str | None = None
    ops: list[dict] = field(default_factory=list)
    superposed: list[int] = field(default_factory=list)  # qubits in superposition

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "a_text": self.a_text,
            "a_bits": self.a_bits,
            "b_text": self.b_text,
            "b_bits": self.b_bits,
            "ops": self.ops,
            "superposed": self.superposed,
        }


def plan(a_text: str, b_text: str | None = None) -> Plan:
    """Build the gate plan for one string, or a superposition of two."""
    if b_text is not None:
        # pad the shorter string with spaces so both fill the same register
        while len(a_text.encode()) < len(b_text.encode()):
            a_text += " "
        while len(b_text.encode()) < len(a_text.encode()):
            b_text += " "
    a = text_to_bits(a_text)
    b = text_to_bits(b_text) if b_text is not None else None
    n = len(a)

    def bit(s: str, q: int) -> bool:
        return s[n - 1 - q] == "1"

    xs, diff = [], []
    for q in range(n):
        if b is None or bit(a, q) == bit(b, q):
            if bit(a, q):
                xs.append(q)
        else:
            diff.append(q)

    ops: list[dict] = []
    if xs:
        ops.append({"g": "x", "q": xs})
    post = []
    if diff:
        d0 = max(diff)
        ops.append({"g": "h", "q": [d0]})
        for d in sorted(diff, reverse=True):
            if d == d0:
                continue
            ops.append({"g": "cx", "c": d0, "t": d})
            if bit(a, d) != bit(a, d0):
                post.append(d)
    if post:
        ops.append({"g": "x", "q": post})

    return Plan(n, a_text, a, b_text, b, ops, sorted(diff, reverse=True))


def build(p: Plan, measure: bool = True) -> QuantumCircuit:
    qr = QuantumRegister(p.n, "qr")
    cr = ClassicalRegister(p.n, "cr")
    qc = QuantumCircuit(qr, cr)
    for op in p.ops:
        if op["g"] == "x":
            qc.x([qr[q] for q in op["q"]])
        elif op["g"] == "h":
            qc.h(qr[op["q"][0]])
        elif op["g"] == "cx":
            qc.cx(qr[op["c"]], qr[op["t"]])
    if measure:
        qc.measure(qr, cr)
    return qc


def _flip(bits: str, p: float, rng: random.Random) -> str:
    if p <= 0:
        return bits
    return "".join(("1" if c == "0" else "0") if rng.random() < p else c for c in bits)


def run(p: Plan, shots: int = 1024, noise: float = 0.0) -> list[tuple[str, int]]:
    """Measure the plan on Aer. Readout noise flips each measured bit with probability `noise`."""
    shots = max(1, min(int(shots), MAX_SHOTS))
    qc = build(p)
    result = _sim.run(qc, shots=shots, memory=noise > 0).result()
    if noise <= 0:
        counts = result.get_counts()
    else:
        rng = random.Random()
        counts: dict[str, int] = {}
        for m in result.get_memory():
            k = _flip(m.replace(" ", ""), noise, rng)
            counts[k] = counts.get(k, 0) + 1
    return sorted(((k.replace(" ", ""), v) for k, v in counts.items()), key=lambda kv: -kv[1])


def measure_once(p: Plan, noise: float = 0.0) -> str:
    return run(p, shots=1, noise=noise)[0][0]


CHUNK_BYTES = 16  # 128-qubit registers


def measure_text(text: str, noise: float = 0.0) -> tuple[str, int]:
    """Send a long text through qubits as a batch of 128-qubit registers, one shot each.

    One giant register would make the stabilizer tableau grow as n^2; batching
    keeps a 480-character reply well under a second. Returns (bits, registers).
    """
    data = text.encode("utf-8")
    chunks = [data[i:i + CHUNK_BYTES] for i in range(0, len(data), CHUNK_BYTES)] or [b" "]
    plans = [_plan_bytes(c) for c in chunks]
    circuits = [build(p) for p in plans]
    result = _sim.run(circuits, shots=1, memory=True).result()
    rng = random.Random()
    bits = "".join(_flip(result.get_memory(i)[0].replace(" ", ""), noise, rng) for i in range(len(circuits)))
    return bits, len(circuits)


def _plan_bytes(data: bytes) -> Plan:
    bits = "".join(f"{b:08b}" for b in data)
    n = len(bits)
    xs = [q for q in range(n) if bits[n - 1 - q] == "1"]
    return Plan(n, data.decode("utf-8", "replace"), bits, ops=[{"g": "x", "q": xs}] if xs else [])


def draw(p: Plan) -> str | None:
    if p.n > MAX_DRAW_QUBITS:
        return None
    return str(build(p, measure=False).draw("text", fold=-1))


def qiskit_code(p: Plan, shots: int = 1024) -> str:
    what = f"|{p.a_text}> + |{p.b_text}>" if p.b_text is not None else f"|{p.a_text}>"
    lines = [
        "from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister",
        "from qiskit_aer import AerSimulator",
        "",
        f"# {what} in {p.n} qubits. qr[0] is the rightmost bit, like answer #1685",
        f'qr = QuantumRegister({p.n}, "qr")',
        f'cr = ClassicalRegister({p.n}, "cr")',
        "qc = QuantumCircuit(qr, cr)",
    ]
    for op in p.ops:
        if op["g"] == "cx":
            lines.append(f"qc.cx(qr[{op['c']}], qr[{op['t']}])")
        elif len(op["q"]) == 1:
            lines.append(f"qc.{op['g']}(qr[{op['q'][0]}])")
        else:
            lines.append(f"qc.{op['g']}([{', '.join(f'qr[{q}]' for q in op['q'])}])")
    lines += [
        "qc.measure(qr, cr)",
        "",
        "# X, H and CNOT are Clifford gates, so the stabilizer method handles big registers",
        f'counts = AerSimulator(method="stabilizer").run(qc, shots={shots}).result().get_counts()',
        "",
        "for bits, n in sorted(counts.items(), key=lambda kv: -kv[1]):",
        '    text = int(bits, 2).to_bytes(len(bits) // 8, "big").decode("utf-8", "replace")',
        "    print(repr(text), n)",
    ]
    return "\n".join(lines)
