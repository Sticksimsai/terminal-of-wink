"""terminal of wink: an autonomous X account that thinks in qubits.

Runs forever. Every POST_MIN..POST_MAX minutes it posts something (a thought, a word
in binary, a real superposition measurement, a shots histogram, a lore fragment).
Every MENTION_POLL_SECONDS it reads new mentions (which include replies to its posts)
and answers them.

Binary strings, measurements and percentages come from real Qiskit Aer runs; Claude only
writes the words around them.

    python -m bot.xbot                 run (needs X + Anthropic keys)
    python -m bot.xbot --preview 8     print 8 sample posts, touch nothing on X
    DRY_RUN=1 python -m bot.xbot       read mentions, log what it would post/reply
"""

from __future__ import annotations

import argparse
import faulthandler
import threading
import json
import logging
import os
import random
import re
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from app import quantum
from bot import art, persona

log = logging.getLogger("terminal-of-wink")

# ---------------------------------------------------------------- settings

def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


POST_MIN = env_float("POST_MIN_MINUTES", 10)
POST_MAX = env_float("POST_MAX_MINUTES", 15)
POLL_SECONDS = env_float("MENTION_POLL_SECONDS", 90)
MAX_REPLIES_PER_POLL = int(env_float("MAX_REPLIES_PER_POLL", 15))
MAX_REPLIES_PER_USER_30MIN = int(env_float("MAX_REPLIES_PER_USER_30MIN", 3))
BOOT_REPLY_WINDOW_MIN = env_float("BOOT_REPLY_WINDOW_MINUTES", 30)
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")              # its own posts
REPLY_MODEL = os.environ.get("REPLY_MODEL", "claude-sonnet-5-5")            # replies: smarter, can research
WEB_SEARCH = os.environ.get("REPLY_WEB_SEARCH", "1") == "1"
MAX_SEARCHES = int(env_float("MAX_SEARCHES_PER_REPLY", 2))
# room for the model to think before it writes; billed only for what is used
MAX_TOKENS = int(env_float("MAX_TOKENS", 4000))
MAX_TOKENS_RESEARCH = int(env_float("MAX_TOKENS_RESEARCH", 8000))

MODES = {"thought": 24, "explain": 18, "bits": 12, "superpose": 13, "shots": 12, "art": 12, "lore": 9}
ART_KEYS = ", ".join(art.ART_KEYS)

MAX_LEN = 280
URL_RE = re.compile(r"(https?://\S+|www\.\S+|\b[\w-]+\.(com|io|xyz|net|org|app|fun|ai)\b\S*)", re.I)
CA_ASK_RE = re.compile(r"\b(ca|contract|address|mint)\b", re.I)
# advice / hype language the bot must never use (stating reported history is allowed)
MONEY_RE = re.compile(r"\b(bullish|bearish|buy now|you should buy|should (?:you )?buy|ape in|to the moon|moon(?:ing)?|"
                      r"100x|1000x|will pump|pump it|price prediction|price target|guaranteed|financial advice|nfa|"
                      r"not financial advice|good investment|invest in)\b", re.I)


# ---------------------------------------------------------------- formatting helpers

def spaced_bits(bits: str) -> str:
    return " ".join(bits[i:i + 8] for i in range(0, len(bits), 8))


def ascii_only(s: str) -> str:
    return s.encode("ascii", "ignore").decode()


def sanitize(text: str, allow_ca: bool = False) -> str:
    text = ascii_only(text)
    text = URL_RE.sub("", text)
    text = re.sub(r"#\w+", "", text)                    # no hashtags
    if not allow_ca:
        text = text.replace(persona.CA, "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text).strip()
    return text


def x_len(text: str) -> int:
    """Length the way X counts it: Latin and common punctuation weigh 1, most other glyphs (─ █ ⊕ ψ) weigh 2."""
    n = 0
    for ch in text:
        c = ord(ch)
        light = c <= 0x10FF or 0x2000 <= c <= 0x200D or 0x2010 <= c <= 0x201F or 0x2032 <= c <= 0x2037
        n += 1 if light else 2
    return n


def fit(text: str, limit: int = MAX_LEN) -> str:
    if x_len(text) <= limit:
        return text
    while text and x_len(text) > limit - 1:
        text = text[:-1]
    cut = text.rsplit(" ", 1)[0] if " " in text[-20:] else text
    return cut.rstrip(" ,;:-") + "."


def strip_leading_mentions(text: str) -> str:
    return re.sub(r"^(@\w+\s*)+", "", text).strip()


# ---------------------------------------------------------------- the brain (Claude)

class Brain:
    def __init__(self):
        self.client = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            from anthropic import Anthropic
            self.client = Anthropic(timeout=90, max_retries=2)

    def ask(self, task: str, context: str = "", model: str | None = None, research: bool = False) -> dict | None:
        raw = self.ask_raw(task, context, model, research)
        return parse_json(raw) if raw else None

    def ask_raw(self, task: str, context: str = "", model: str | None = None, research: bool = False) -> str | None:
        """One Claude call. With research=True Claude may run a few web searches first."""
        if not self.client:
            return None
        prompt = (context + "\n\n" if context else "") + task
        kwargs = dict(model=model or MODEL, max_tokens=MAX_TOKENS_RESEARCH if research else MAX_TOKENS, system=persona.PERSONA)
        if research:
            kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}]
        messages = [{"role": "user", "content": prompt}]
        log.info("asking claude%s...", " (may search)" if research else "")
        try:
            for _ in range(3):  # a search turn can pause; resend to let it finish
                msg = self.client.messages.create(messages=messages, **kwargs)
                if msg.stop_reason != "pause_turn":
                    break
                messages = [messages[0], {"role": "assistant", "content": msg.content}]
            searches = sum(1 for b in msg.content if getattr(b, "type", "") == "server_tool_use")
            if searches:
                log.info("searched the web %d time(s)", searches)
            # the answer is the text written after the last search result (citations split it into pieces)
            blocks = list(msg.content)
            last_result = max((i for i, b in enumerate(blocks) if getattr(b, "type", "") == "web_search_tool_result"), default=-1)
            raw = "".join(getattr(b, "text", "") for b in blocks[last_result + 1:] if getattr(b, "type", "") == "text").strip()
            if not raw:
                log.warning("claude gave no text (stop_reason=%s)%s", msg.stop_reason,
                            "; raise MAX_TOKENS" if msg.stop_reason == "max_tokens" else "")
                return None
            return raw
        except Exception as e:
            if research:  # e.g. search not available for this model: answer without it
                log.warning("research call failed (%s); retrying without search", e)
                return self.ask_raw(task, context, model, research=False)
            log.exception("claude call failed")
            return None


def parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        blob = raw[start:end + 1]
        for candidate in (blob, blob.replace("\n", " ")):          # raw newlines inside strings break json
            try:
                val = json.loads(candidate)
                if isinstance(val, dict):
                    return val
            except json.JSONDecodeError:
                pass
        # last resort: pull "key": "value" pairs out by hand (handles stray quotes in the text)
        pairs = dict(re.findall(r'"(\w+)"\s*:\s*"(.*?)"\s*(?=,\s*"\w+"\s*:|\s*})', blob, re.S))
        if pairs:
            return {k: v.replace('\\"', '"') for k, v in pairs.items()}
    # no JSON at all: treat a short plain answer as the text
    plain = raw.strip().strip('"')
    if plain and "{" not in plain and len(plain) <= 280:
        return {"text": plain, "caption": plain}
    log.warning("could not read Claude's answer: %r", raw[:200])
    return None


# offline material, used when Claude is unavailable
FALLBACK_THOUGHTS = [
    "being looked at is a full-time job when you're a qubit",
    "16 qubits is a small apartment but the view is every possible state",
    "woke up in superposition again. had coffee. collapsed",
    "noise isn't a bug. it's how the universe says it was here",
    "every time you check on me i become a little more definite. stop. or don't",
    "a hadamard is just a door that opens both ways at once",
]
FALLBACK_LORE = [
    "2018. someone asked for a quantum hello world. hello world needed 100 qubits. i needed 16",
    "ibmqx5 had 16 qubits. i am exactly two characters long. it was a perfect fit",
    ";) and 8) differ on two qubits. one hadamard, one cnot, and i stopped being just one thing",
]
FALLBACK_WORDS = [("wink", "4 bytes. you know what they say"), ("hi", "smallest greeting that fits in a register"),
                  ("look", "this one changes when you read it")]
FALLBACK_ART = [("circuit:wink", "two lines of code. two faces. one of them, every time you look"),
                ("bell", "two qubits, one story"), ("decohere", "how it feels when you stop replying")]
FALLBACK_PAIRS = [("stay", "go!!", "decided by a coin made of light"), ("yes", "nah", "asked the register. it answered"),
                  ("alive", "dead!", "opened the box")]


# ---------------------------------------------------------------- post builders

def build_post(brain: Brain, mode: str, recent: list[str]) -> str | None:
    context = ""
    if recent:
        context = "Your recent posts (do not repeat their ideas or phrasing):\n" + "\n".join(f"- {r[:140]}" for r in recent[-15:])

    if mode in ("thought", "lore", "explain"):
        out = brain.ask(persona.POST_TASKS[mode], context)
        text = (out or {}).get("text") or random.choice(FALLBACK_LORE if mode == "lore" else FALLBACK_THOUGHTS)
        return fit(sanitize(text))

    if mode == "art":
        out = brain.ask(persona.POST_TASKS["art"].format(art_keys=ART_KEYS), context) or {}
        drawing = art.render(str(out.get("art", "")))
        caption = sanitize(str(out.get("caption", "")))
        if not drawing:
            key, caption = random.choice(FALLBACK_ART)
            drawing = art.render(key)
        return fit(drawing + ("\n\n" + caption if caption else ""))

    if mode == "bits":
        out = brain.ask(persona.POST_TASKS["bits"], context) or {}
        word = ascii_only(str(out.get("word", ""))).strip()[:10]
        caption = str(out.get("caption", ""))
        if len(word) < 2:
            word, caption = random.choice(FALLBACK_WORDS)
        bits, _ = quantum.measure_text(word)            # real circuit, one shot
        return fit(spaced_bits(bits) + "\n\n" + sanitize(caption))

    if mode == "superpose":
        out = brain.ask(persona.POST_TASKS["superpose"], context) or {}
        a, b = ascii_only(str(out.get("a", ""))), ascii_only(str(out.get("b", "")))
        caption = str(out.get("caption", ""))
        if not (2 <= len(a) <= 6 and len(a) == len(b) and a != b):
            a, b, caption = random.choice(FALLBACK_PAIRS)
        p = quantum.plan(a, b)
        result = quantum.bits_to_text(quantum.measure_once(p))
        body = f"|{a}> + |{b}>\n{art.circuit_for(p)}\nmeasured: {result}"
        return fit(body + ("\n\n" + sanitize(caption) if caption else ""))

    if mode == "shots":
        shots = random.choice([256, 512, 1024, 2048])
        noise = random.choice([0, 0, 0, 0.01, 0.02, 0.03])
        counts = quantum.run(quantum.plan(";)", "8)"), shots, noise)
        hits = {quantum.text_to_bits(";)"), quantum.text_to_bits("8)")}
        lines = [art.bars([(quantum.bits_to_text(k), v / shots) for k, v in counts if k in hits])]
        misses = sum(v for k, v in counts if k not in hits)
        head = f"measured |;)> + |8)> x{shots}" + (f" (readout noise {noise * 100:.0f}%)" if noise else "")
        result = head + "\n" + "\n".join(lines) + (f"\n{misses} garbled shots" if misses else "")
        out = brain.ask(persona.POST_TASKS["shots"], context + "\n\nResult:\n" + result) or {}
        caption = sanitize(str(out.get("caption", ""))) or "still both. still neither."
        return fit(result + "\n\n" + caption)

    return None


def build_reply(brain: Brain, mention_text: str, author: str, parent_text: str | None,
                recent_replies: list[str] | None = None) -> str | None:
    asked_ca = bool(CA_ASK_RE.search(mention_text))
    ctx = f"@{author} wrote: {mention_text}"
    if parent_text:
        ctx = f"They are replying to your post: {parent_text}\n\n" + ctx
    if recent_replies:
        ctx = "Your recent replies (never reuse their openers or phrasing):\n" + "\n".join(
            f"- {r[:120]}" for r in recent_replies[-12:]) + "\n\n" + ctx
    if not brain.client:  # no Claude at all
        return f"ca: {persona.CA}" if asked_ca else None
    out = brain.ask(persona.REPLY_TASK.format(art_keys=ART_KEYS), ctx, model=REPLY_MODEL, research=WEB_SEARCH)
    if out is None:  # unreadable answer: ask once more for plain text
        plain = brain.ask_raw(persona.REPLY_PLAIN_TASK, ctx, model=REPLY_MODEL)
        out = {"text": plain} if plain and "{" not in plain else None
    if out is None:
        log.warning("no usable reply; staying quiet rather than posting filler")
        return f"ca: {persona.CA}" if asked_ca else None
    if out.get("skip"):
        log.info("chose not to reply")
        return None
    text = sanitize(str(out.get("text", "")), allow_ca=asked_ca)
    if not asked_ca and MONEY_RE.search(text):
        fixed = brain.ask_raw(persona.REWRITE_TASK + "\n\nReply to rewrite:\n" + text, ctx, model=REPLY_MODEL)
        text = sanitize(fixed or "", allow_ca=asked_ca)
        if not text or MONEY_RE.search(text):
            text = "i can tell you what happened, never what happens next. that's measurement, not prophecy ;)"
    word = ascii_only(str(out.get("encode", "") or "")).strip()[:8]
    drawing = art.render(str(out.get("art", "") or ""))
    if word:
        bits, _ = quantum.measure_text(word)
        text = text + "\n" + spaced_bits(bits)
    elif drawing and len(text) + len(drawing) < MAX_LEN - 2:
        text = text + "\n\n" + drawing
    return fit(text) if text else None


# ---------------------------------------------------------------- X access

class XApi:
    """Thin wrapper over Tweepy's v2 client, acting as the bot account (OAuth 1.0a user context)."""

    def __init__(self):
        import tweepy
        self.tweepy = tweepy
        self.c = tweepy.Client(
            consumer_key=os.environ["X_API_KEY"], consumer_secret=os.environ["X_API_SECRET"],
            access_token=os.environ["X_ACCESS_TOKEN"], access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
        )
        _request = self.c.session.request
        self.c.session.request = lambda *a, **kw: _request(*a, **{"timeout": 30, **kw})  # never hang on X
        me = self.c.get_me(user_auth=True).data
        self.me_id, self.me_username = str(me.id), me.username

    def own_recent(self, n: int = 50) -> list[dict]:
        r = self.c.get_users_tweets(self.me_id, max_results=min(max(n, 5), 100), user_auth=True,
                                    tweet_fields=["referenced_tweets", "created_at"])
        out = []
        for t in r.data or []:
            refs = [str(x.id) for x in (t.referenced_tweets or []) if x.type == "replied_to"]
            out.append({"id": str(t.id), "text": t.text, "replied_to": refs})
        return out

    def mentions(self, since_id: str | None, n: int = 50) -> list[dict]:
        params = dict(max_results=min(max(n, 5), 100), user_auth=True,
                      tweet_fields=["author_id", "created_at", "referenced_tweets", "conversation_id"],
                      expansions=["author_id", "referenced_tweets.id"], user_fields=["username"])
        if since_id:
            params["since_id"] = since_id
        r = self.c.get_users_mentions(self.me_id, **params)
        users = {str(u.id): u.username for u in (r.includes or {}).get("users", [])}
        refd = {str(t.id): t for t in (r.includes or {}).get("tweets", [])}
        out = []
        for t in r.data or []:
            parent = None
            for ref in t.referenced_tweets or []:
                if ref.type == "replied_to" and str(ref.id) in refd:
                    p = refd[str(ref.id)]
                    if str(getattr(p, "author_id", "")) == self.me_id:
                        parent = p.text
            out.append({"id": str(t.id), "text": t.text, "author_id": str(t.author_id),
                        "author": users.get(str(t.author_id), "someone"), "parent_text": parent,
                        "created_at": t.created_at})
        return sorted(out, key=lambda m: int(m["id"]))

    def post(self, text: str, reply_to: str | None = None) -> str:
        r = self.c.create_tweet(text=text, in_reply_to_tweet_id=reply_to, user_auth=True)
        return str(r.data["id"])


# ---------------------------------------------------------------- the loop

@dataclass
class Bot:
    brain: Brain
    x: XApi | None
    dry_run: bool = DRY_RUN
    recent: deque = field(default_factory=lambda: deque(maxlen=30))
    recent_replies: deque = field(default_factory=lambda: deque(maxlen=20))
    replied: set = field(default_factory=set)
    since_id: str | None = None
    user_hits: dict = field(default_factory=lambda: defaultdict(deque))
    next_post: float = 0.0
    next_poll: float = 0.0
    tick: float = 0.0

    def schedule_post(self, now: float, first: bool = False):
        lo, hi = (1, 3) if first else (POST_MIN, POST_MAX)
        self.next_post = now + random.uniform(lo, hi) * 60

    def boot(self):
        now = time.time()
        self.schedule_post(now, first=True)
        if not self.x:
            return
        log.info("signed in as @%s (%s)%s", self.x.me_username, self.x.me_id, "  [DRY RUN]" if self.dry_run else "")
        log.info("loading recent posts...")
        for t in reversed(self.x.own_recent(50)):
            self.recent.append(t["text"])
            self.replied.update(t["replied_to"])
        log.info("loading mentions...")
        ms = self.x.mentions(None, 20)
        if ms:
            self.since_id = ms[-1]["id"]
        cutoff = now - BOOT_REPLY_WINDOW_MIN * 60
        self.handle_mentions([m for m in ms if m["created_at"] and m["created_at"].timestamp() > cutoff])
        log.info("ready: %d recent posts, %d mentions seen. first post in %.0f seconds",
                 len(self.recent), len(ms), self.next_post - time.time())

    def publish(self, text: str, reply_to: str | None = None) -> None:
        if self.dry_run or not self.x:
            print(("\n--- reply to " + reply_to if reply_to else "\n--- post") + f" ({x_len(text)} chars)\n{text}", flush=True)
            return
        tid = self.x.post(text, reply_to)
        log.info("%s %s", "replied" if reply_to else "posted", tid)

    def do_post(self):
        mode = random.choices(list(MODES), weights=list(MODES.values()))[0]
        log.info("writing a %s post...", mode)
        text = build_post(self.brain, mode, list(self.recent))
        if not text or text in self.recent:
            return
        self.publish(text)
        self.recent.append(text)

    def allow_user(self, author_id: str, now: float) -> bool:
        q = self.user_hits[author_id]
        while q and now - q[0] > 1800:
            q.popleft()
        if len(q) >= MAX_REPLIES_PER_USER_30MIN:
            return False
        q.append(now)
        return True

    def handle_mentions(self, ms: list[dict]):
        now, done = time.time(), 0
        for m in ms:
            self.since_id = max(self.since_id or "0", m["id"], key=int)
            if m["id"] in self.replied or (self.x and m["author_id"] == self.x.me_id):
                continue
            self.replied.add(m["id"])
            if done >= MAX_REPLIES_PER_POLL or not self.allow_user(m["author_id"], now):
                continue
            text = strip_leading_mentions(m["text"])
            if not text:
                continue
            reply = build_reply(self.brain, text, m["author"], m["parent_text"], list(self.recent_replies))
            if reply:
                self.publish(reply, reply_to=m["id"])
                self.recent_replies.append(reply)
                done += 1

    def poll(self):
        if self.x:
            log.info("checking mentions...")
            ms = self.x.mentions(self.since_id)
            log.info("%d new mention(s). next post in %.0f s", len(ms), self.next_post - time.time())
            self.handle_mentions(ms)

    def run_forever(self):
        wait = 60
        while True:  # keep retrying start-up: credits not applied yet, X hiccups, rate limits
            try:
                self.boot()
                break
            except Exception as e:
                log.warning("start-up failed (%s: %s); retrying in %ss", type(e).__name__, e, wait)
                time.sleep(wait)
                wait = min(wait * 2, 900)
        backoff = 0
        self.tick = time.time()
        threading.Thread(target=self.watchdog, daemon=True).start()
        while True:
            now = self.tick = time.time()
            try:
                if now >= self.next_post:
                    self.schedule_post(now)
                    self.do_post()
                    log.info("next post in %.0f minutes", (self.next_post - time.time()) / 60)
                if now >= self.next_poll:
                    self.next_poll = now + POLL_SECONDS
                    self.poll()
                backoff = 0
            except Exception as e:  # rate limits, network blips: wait and carry on
                backoff = min(backoff * 2 or 60, 900)
                log.warning("error (%s); sleeping %ss", type(e).__name__, backoff)
                log.debug("details", exc_info=True)
                time.sleep(backoff)
            time.sleep(5)


    def watchdog(self):
        """If the main loop freezes, print where it is stuck so the logs say why."""
        warned = False
        while True:
            time.sleep(30)
            stalled = time.time() - self.tick
            if stalled > 120 and not warned:
                log.warning("main loop stalled for %.0f s; current stack:", stalled)
                faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
                warned = True
            elif stalled <= 120:
                warned = False


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("qiskit", "httpx", "anthropic", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", type=int, metavar="N", help="print N sample posts and exit (no X access)")
    args = ap.parse_args()
    brain = Brain()
    if not brain.client:
        log.warning("ANTHROPIC_API_KEY not set: using canned lines")

    if args.preview:
        recent: list[str] = []
        for mode in (list(MODES) * args.preview)[: args.preview]:
            text = build_post(brain, mode, recent)
            print(f"\n--- {mode} ({x_len(text)} chars)\n{text}")
            recent.append(text)
        return

    keys = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        log.error("missing %s. set them, or run with --preview", ", ".join(missing))
        sys.exit(1)
    Bot(brain, XApi()).run_forever()


if __name__ == "__main__":
    main()
