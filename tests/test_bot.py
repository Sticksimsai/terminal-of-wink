from datetime import datetime, timezone

from app import quantum
from bot import persona, xbot


class FakeBrain:
    """Stands in for Claude: returns whatever the test queues up."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.client = object()
        self.prompts = []

    def ask(self, task, context="", model=None, research=False):
        self.prompts.append(context + task)
        return self.answers.pop(0) if self.answers else None

    def ask_raw(self, task, context="", model=None, research=False):
        self.prompts.append(context + task)
        return None


class FakeX:
    me_id, me_username = "1", "terminalofwink"

    def __init__(self, mentions):
        self._mentions = mentions
        self.posts = []

    def own_recent(self, n=50):
        return [{"id": "100", "text": "old post", "replied_to": ["5"]}]

    def mentions(self, since_id, n=50):
        return [m for m in self._mentions if since_id is None or int(m["id"]) > int(since_id)]

    def post(self, text, reply_to=None):
        self.posts.append((text, reply_to))
        return str(1000 + len(self.posts))


def mention(i, text, author_id="42", author="anon", parent=None):
    return {"id": str(i), "text": text, "author_id": author_id, "author": author,
            "parent_text": parent, "created_at": datetime.now(timezone.utc)}


def test_sanitize_strips_links_hashtags_and_ca():
    t = xbot.sanitize(f"go to https://x.com and terminalofwink.com #quantum {persona.CA} ;)")
    assert "http" not in t and ".com" not in t and "#" not in t and persona.CA not in t
    assert ";)" in t


def test_ca_only_when_asked():
    brain = FakeBrain({"skip": False, "text": f"here: {persona.CA}", "encode": ""})
    assert persona.CA in xbot.build_reply(brain, "what's the ca?", "anon", None)
    brain = FakeBrain({"skip": False, "text": f"random {persona.CA}", "encode": ""})
    assert persona.CA not in xbot.build_reply(brain, "hello", "anon", None)


def test_money_talk_is_deflected():
    brain = FakeBrain({"skip": False, "text": "buy now, it will moon", "encode": ""})
    r = xbot.build_reply(brain, "should i buy?", "anon", None)
    assert "moon" not in r and "buy" not in r


def test_reply_can_append_real_bits():
    brain = FakeBrain({"skip": False, "text": "here is a wink", "encode": "wink"})
    r = xbot.build_reply(brain, "show me", "anon", None)
    assert xbot.spaced_bits(quantum.text_to_bits("wink")) in r


def test_superpose_post_uses_a_real_measurement():
    brain = FakeBrain({"a": "yes", "b": "nah", "caption": "asked the register"})
    t = xbot.build_post(brain, "superpose", [])
    assert "|yes> + |nah>" in t
    assert t.split("measured: ")[1].split("\n")[0] in {"yes", "nah"}
    assert len(t) <= 280


def test_bot_replies_once_skips_self_and_already_replied():
    x = FakeX([
        mention(5, "@terminalofwink old one"),                       # already replied (in own_recent)
        mention(6, "@terminalofwink hi there"),
        mention(7, "@terminalofwink talking to myself", author_id="1"),
    ])
    brain = FakeBrain({"skip": False, "text": "hello human", "encode": ""})
    bot = xbot.Bot(brain, x, dry_run=False)
    bot.boot()
    assert x.posts == [("hello human", "6")]
    bot.poll()                                                      # nothing new, nothing re-sent
    assert len(x.posts) == 1
    assert bot.since_id == "7"


def test_per_user_limit():
    x = FakeX([mention(i, "@terminalofwink spam") for i in range(10, 20)])
    brain = FakeBrain(*[{"skip": False, "text": f"r{i}", "encode": ""} for i in range(10)])
    bot = xbot.Bot(brain, x, dry_run=False)
    bot.boot()
    assert len(x.posts) == xbot.MAX_REPLIES_PER_USER_30MIN


def test_reply_can_draw_art_and_remembers_recent_replies():
    brain = FakeBrain({"skip": False, "text": "two qubits, one fate", "encode": "", "art": "circuit:bell"})
    r = xbot.build_reply(brain, "what is a bell state", "anon", None, ["earlier reply about cats"])
    assert "─H──●──M" in r
    assert "earlier reply about cats" in brain.prompts[0]


def test_x_len_counts_wide_glyphs_double_and_fit_respects_it():
    assert xbot.x_len("abc") == 3
    assert xbot.x_len("█─⊕") == 6
    assert xbot.x_len(xbot.fit("█" * 300)) <= 280


def test_unreadable_answer_means_silence_not_filler():
    brain = FakeBrain()  # every call comes back empty
    assert xbot.build_reply(brain, "how high did truth terminal go", "anon", None) is None


def test_reported_history_allowed_but_hype_rewritten():
    brain = FakeBrain({"skip": False, "text": "truth terminal's goat token passed a big market cap in 2024", "encode": "", "art": ""})
    assert "market cap" in xbot.build_reply(brain, "how high did it go", "anon", None)
    brain = FakeBrain({"skip": False, "text": "very bullish, ape in", "encode": "", "art": ""})
    r = xbot.build_reply(brain, "bullish?", "anon", None)
    assert "bullish" not in r and "ape" not in r
