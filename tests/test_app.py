import json

from fastapi.testclient import TestClient

from app import quantum
from app.main import app

client = TestClient(app)


def test_wink_bits_match_answer_1685():
    assert quantum.text_to_bits(";)") == "0011101100101001"
    assert quantum.text_to_bits("8)") == "0011100000101001"


def test_superposition_uses_hadamard_and_cnot_on_qubits_9_and_8():
    p = quantum.plan(";)", "8)")
    assert p.superposed == [9, 8]
    assert {"g": "h", "q": [9]} in p.ops
    assert {"g": "cx", "c": 9, "t": 8} in p.ops


def test_superposition_only_yields_the_two_strings():
    counts = dict(quantum.run(quantum.plan("cat", "dog"), shots=500))
    assert set(counts) <= {quantum.text_to_bits("cat"), quantum.text_to_bits("dog")}
    assert len(counts) == 2


def test_long_text_round_trips_through_qubits():
    text = "the wink is loaded, say something " * 10
    bits, regs = quantum.measure_text(text)
    assert quantum.bits_to_text(bits) == text
    assert regs == -(-len(text.encode()) // quantum.CHUNK_BYTES)


def test_run_endpoint():
    r = client.post("/api/run", json={"a": ";)", "b": "8)", "shots": 256})
    assert r.status_code == 200
    body = r.json()
    assert {c["text"] for c in body["counts"]} == {";)", "8)"}
    assert "qc.h(qr[9])" in body["qiskit"]


def test_run_rejects_oversized_text():
    r = client.post("/api/run", json={"a": "x" * 40})
    assert r.status_code == 422


def test_chat_streams_and_measures_offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "what is ;) ?"}]})
    assert r.status_code == 200
    events = [blk for blk in r.text.split("\n\n") if blk.strip()]
    done = json.loads(events[-1].split("data: ", 1)[1])
    assert done["offline"] and done["lore"]
    assert done["measured_text"] == done["reply"]
    assert done["wink"] in {";)", "8)"}
