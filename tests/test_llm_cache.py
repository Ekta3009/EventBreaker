"""Cached Nemotron call: same messages → stored answer; invalid answers are not stored; fresh re-asks."""
from types import SimpleNamespace

from eventbreaker.agent import llm


class FakeOpenAI:
    calls = 0
    reply = "[]"

    def __init__(self, **_):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        FakeOpenAI.calls += 1
        assert kwargs["temperature"] == 0
        msg = SimpleNamespace(content=FakeOpenAI.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(llm.usage, "record", lambda *a: None)
    monkeypatch.setenv("NEBIUS_API_KEY", "test")
    FakeOpenAI.calls, FakeOpenAI.reply = 0, "[]"


MSGS = [{"role": "user", "content": "hi"}]


def test_same_messages_answered_from_cache(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    assert llm.chat(llm.SUPER, MSGS, "t") == llm.chat(llm.SUPER, MSGS, "t") == "[]"
    assert FakeOpenAI.calls == 1


def test_fresh_and_different_messages_ask_again(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    llm.chat(llm.SUPER, MSGS, "t")
    llm.chat(llm.SUPER, MSGS, "t", fresh=True)
    llm.chat(llm.ULTRA, MSGS, "t")
    assert FakeOpenAI.calls == 3


def test_invalid_answer_not_cached(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    FakeOpenAI.reply = "no json"
    llm.chat(llm.SUPER, MSGS, "t", valid=lambda r: r.startswith("["))
    llm.chat(llm.SUPER, MSGS, "t", valid=lambda r: r.startswith("["))
    assert FakeOpenAI.calls == 2
