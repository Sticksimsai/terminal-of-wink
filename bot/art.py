"""Small ASCII/ket art for X posts.

X shows posts in a proportional font, so nothing here relies on lining up spaces.
Lines are built from glyphs of similar width (─ ● ⊕ █) and kets, which survive any font.
Art that depends on data (bars, circuits) is drawn from real Qiskit results.
"""

from __future__ import annotations

from app import quantum

KETS = {
    "plus":     "|+> = (|0> + |1>)/√2",
    "minus":    "|-> = (|0> - |1>)/√2",
    "bell":     "|Φ+> = (|00> + |11>)/√2",
    "ghz":      "|GHZ> = (|000> + |111>)/√2",
    "wink":     "|ψ> = (|;)> + |8)>)/√2",
    "cat":      "|cat> = (|alive> + |dead>)/√2",
    "qubit":    "|ψ> = α|0> + β|1>,  |α|² + |β|² = 1",
    "grover":   "amplitude: ▁▁▁█▁▁▁▁  → ▁▁▁▇▁▁▁▁ → measure",
    "decohere": "|ψ> ∿∿∿∿∿∿∿∿ ∿∿∿ ∿ · .",
    "measure":  "|ψ> ──M══ 0 or 1, never both again",
}

CIRCUITS = {
    "bell":     "q0 ─H──●──M\nq1 ─────⊕──M",
    "ghz":      "q0 ─H──●──●──M\nq1 ─────⊕──┼──M\nq2 ────────⊕──M",
    "wink":     "qr[9] ─H──●──M\nqr[8] ─────⊕──M",
    "teleport": "ψ  ──●──H──M\nq1 ──⊕─────M\nq2 ─────────X──Z──→ ψ",
    "phase":    "q0 ─H──Z──H──M  (comes back as 1)",
    "deutsch":  "q0 ─H──[Uf]──H──M\nq1 ─X──H──[Uf]────",
}

ART_KEYS = sorted(set(KETS) | {f"circuit:{k}" for k in CIRCUITS})


def render(key: str) -> str | None:
    """Return the art for a key like 'bell' (ket) or 'circuit:bell'."""
    key = (key or "").strip().lower()
    if key.startswith("circuit:"):
        return CIRCUITS.get(key.split(":", 1)[1])
    return KETS.get(key)


def bars(rows: list[tuple[str, float]], width: int = 12) -> str:
    """Block-character bars, e.g. ';) ██████▌ 51.2%'. rows = [(label, fraction)]."""
    top = max((f for _, f in rows), default=1) or 1
    out = []
    for label, f in rows:
        n = f / top * width
        full, half = int(n), (n - int(n)) >= 0.5
        out.append(f"{label} {'█' * full}{'▌' if half else ''} {f * 100:.1f}%")
    return "\n".join(out)


def circuit_for(p: quantum.Plan) -> str:
    """Two-or-three-line drawing of a superposition plan: H on one qubit, CNOTs onto the rest."""
    if not p.superposed:
        return ""
    d0, rest = p.superposed[0], p.superposed[1:]
    k = min(len(rest), 2)
    lines = [f"qr[{d0}] ─H──" + "●──" * k + "M"]
    for i, d in enumerate(rest[:k]):
        lines.append(f"qr[{d}] ────" + "───" * i + "⊕──" + "───" * (k - i - 1) + "M")
    if len(rest) > 2:
        lines.append(f"+{len(rest) - 2} more cnots")
    return "\n".join(lines)
