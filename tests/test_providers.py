"""Provider adapters, tested against fake clients rather than a live model.

Both provider classes had no direct coverage at all, which is exactly why a
missing guard survived in one of them: `generate()` indexed `choices[0]`
without checking, while `generate_stream()` five lines below had a comment
explaining why that is unsafe. These tests pin the contract both adapters owe
their callers — always return a str, never raise IndexError on a well-formed
but empty response — so the two paths cannot drift apart again.

Nothing here touches the network: each test injects a fake client object
shaped like the real SDK's response types.
"""

from types import SimpleNamespace

import pytest

from app.llm.ollama_provider import OllamaProvider
from app.llm.openai_compat import OpenAICompatProvider


def _openai_response(choices):
    return SimpleNamespace(choices=choices)


def _openai_choice(content):
    return SimpleNamespace(message=SimpleNamespace(content=content))


class _FakeOpenAIClient:
    """Shaped like the bits of the openai SDK the provider actually touches."""

    def __init__(self, response=None, chunks=None):
        self._response = response
        self._chunks = chunks or []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        return iter(self._chunks) if kwargs.get("stream") else self._response


def _openai_provider(client):
    provider = OpenAICompatProvider(model="m", base_url="http://x", api_key="k")
    provider._client = client
    return provider


class _FakeOllamaClient:
    def __init__(self, response=None, chunks=None):
        self._response = response
        self._chunks = chunks or []

    def chat(self, **kwargs):
        return iter(self._chunks) if kwargs.get("stream") else self._response


def _ollama_provider(client):
    provider = OllamaProvider(model="m")
    provider._client = client
    return provider


def _ollama_message(content):
    return SimpleNamespace(message=SimpleNamespace(content=content))


# ── openai_compat ────────────────────────────────────────────────────────────

def test_openai_generate_returns_the_message_content():
    provider = _openai_provider(
        _FakeOpenAIClient(response=_openai_response([_openai_choice("hello")]))
    )
    assert provider.generate("q", system_prompt="sys") == "hello"


def test_openai_generate_explains_an_empty_choices_list():
    # Regression: a filtered response has choices == [], and indexing it blindly
    # produced "LLM error: list index out of range" — which tells a user
    # nothing. The streaming path already guarded this; generate() did not.
    provider = _openai_provider(_FakeOpenAIClient(response=_openai_response([])))
    with pytest.raises(RuntimeError, match="no choices"):
        provider.generate("q")


def test_openai_generate_never_returns_none():
    # The API types content as optional; the ABC promises a str.
    provider = _openai_provider(
        _FakeOpenAIClient(response=_openai_response([_openai_choice(None)]))
    )
    assert provider.generate("q") == ""


def test_openai_stream_skips_the_usage_only_final_chunk():
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="a"))]),
        SimpleNamespace(choices=[]),                       # Azure-style trailer
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="b"))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]),
    ]
    provider = _openai_provider(_FakeOpenAIClient(chunks=chunks))
    assert list(provider.generate_stream("q")) == ["a", "b"]


def test_openai_is_available_is_false_when_the_endpoint_raises():
    class _Broken:
        models = SimpleNamespace(list=lambda: (_ for _ in ()).throw(OSError("refused")))

    assert _openai_provider(_Broken()).is_available() is False


# ── ollama ───────────────────────────────────────────────────────────────────

def test_ollama_generate_returns_the_message_content():
    provider = _ollama_provider(_FakeOllamaClient(response=_ollama_message("hi")))
    assert provider.generate("q", system_prompt="sys") == "hi"


def test_ollama_generate_never_returns_none():
    # ollama types Message.content as Optional[str]; returning None straight
    # through renders an empty answer bubble with no explanation.
    provider = _ollama_provider(_FakeOllamaClient(response=_ollama_message(None)))
    assert provider.generate("q") == ""


def test_ollama_stream_yields_only_non_empty_chunks():
    chunks = [_ollama_message("a"), _ollama_message(None), _ollama_message("b")]
    provider = _ollama_provider(_FakeOllamaClient(chunks=chunks))
    assert list(provider.generate_stream("q")) == ["a", "b"]


def test_ollama_is_available_is_false_when_the_daemon_is_down():
    class _Broken:
        def list(self):
            raise ConnectionError("connection refused")

    assert _ollama_provider(_Broken()).is_available() is False


# ── The contract both adapters share ─────────────────────────────────────────

def test_both_providers_inject_the_same_system_prompt():
    # Prompt construction lives in the shared, non-abstract base so it cannot
    # drift between providers. This pins that it actually reaches the wire.
    from app.llm.base import ANSWER_SYSTEM_PROMPT

    seen = {}

    class _Capturing(_FakeOpenAIClient):
        def _create(self, **kwargs):
            seen["openai"] = kwargs["messages"]
            return _openai_response([_openai_choice("x")])

    class _CapturingOllama(_FakeOllamaClient):
        def chat(self, **kwargs):
            seen["ollama"] = kwargs["messages"]
            return _ollama_message("x")

    _openai_provider(_Capturing()).answer_question("how many rows?", "FACTS")
    _ollama_provider(_CapturingOllama()).answer_question("how many rows?", "FACTS")

    for messages in seen.values():
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == ANSWER_SYSTEM_PROMPT
        assert "FACTS" in messages[1]["content"]
        assert "how many rows?" in messages[1]["content"]


# ── Reasoning models: <think> must never reach the user or the validator ────

def test_strip_reasoning_removes_a_complete_think_block():
    from app.llm.reasoning import strip_reasoning

    assert strip_reasoning(
        "<think>Maybe 1234? No, that's the row count.</think>The mean is 42.0."
    ) == "The mean is 42.0."


def test_strip_reasoning_leaves_an_ordinary_answer_untouched():
    from app.llm.reasoning import strip_reasoning

    text = "The mean revenue is 221.89, and 3 < 5 for the median."
    assert strip_reasoning(text) == text


def test_an_unterminated_think_block_yields_no_answer():
    # A model cut off mid-thought never produced an answer. Emitting the
    # scratchpad instead would be worse than emitting nothing: the UI has a
    # "response cut off" state, and the validator would score the scratchpad.
    from app.llm.reasoning import strip_reasoning

    assert strip_reasoning("<think>still deciding, maybe 9999") == ""


def test_reasoning_filter_survives_a_tag_split_across_chunks():
    # The real failure mode in streaming: "<think>" arrives as "<thi" + "nk>".
    # A naive per-chunk replace would pass the scratchpad straight through.
    from app.llm.reasoning import ReasoningFilter

    f = ReasoningFilter()
    chunks = ["The answer", " is", "<thi", "nk>discard 9", "99</thi", "nk> 42.0."]
    out = "".join(f.feed(c) for c in chunks) + f.flush()
    assert out == "The answer is 42.0."
    assert "999" not in out


def test_reasoning_filter_handles_several_blocks():
    from app.llm.reasoning import ReasoningFilter

    f = ReasoningFilter()
    out = f.feed("<think>a</think>One.<think>b</think> Two.") + f.flush()
    assert out == "One. Two."


def test_scratchpad_numbers_would_otherwise_be_scored_as_unsupported():
    # The reason this module exists, asserted rather than described: a
    # reasoning model's discarded figures match no fact, so every one of them
    # becomes an "unsupported" claim about an answer that is in fact correct.
    import pandas as pd

    from app.llm.context import build_context
    from app.llm.reasoning import strip_reasoning
    from app.llm.validation import validate_answer

    df = pd.DataFrame({"revenue": [100.0, 200.0, 300.0, 400.0] * 25})
    context = build_context(df)
    mean = df["revenue"].mean()
    raw = (
        f"<think>Could be 918273.0, or maybe 45678.0? Let me recompute.</think>"
        f"The average revenue is {mean:.1f}."
    )

    before = validate_answer(raw, context)
    after = validate_answer(strip_reasoning(raw), context)

    assert before.unsupported_count == 2      # both scratchpad figures
    assert after.unsupported_count == 0
    assert after.verified_count == 1
    assert after.trustworthy is True


def _fake_provider(text: str, chunks: list[str] | None = None):
    """Minimal concrete LLMProvider, to test the base class's own behaviour."""
    from app.llm.base import LLMProvider

    class _Fake(LLMProvider):
        def generate(self, prompt, system_prompt=None):
            return text

        def generate_stream(self, prompt, system_prompt=None):
            yield from (chunks if chunks is not None else [text])

        def is_available(self):
            return True

        @property
        def name(self):
            return "fake"

    return _Fake()


def test_answer_question_strips_reasoning_for_every_provider():
    # Applied in the base class on purpose: a provider added later inherits
    # it rather than having to remember.
    provider = _fake_provider("<think>scratch 999</think>The mean is 42.0.")
    assert provider.answer_question("q", "ctx") == "The mean is 42.0."


def test_answer_question_stream_strips_reasoning_across_chunk_boundaries():
    provider = _fake_provider("", ["<thi", "nk>scratch 999</thi", "nk>Mean", " 42.0."])
    assert "".join(provider.answer_question_stream("q", "ctx")) == "Mean 42.0."
