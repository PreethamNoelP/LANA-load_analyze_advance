"""Strip a reasoning model's private scratchpad out of its answer.

deepseek-r1, qwen3 and similar models emit their chain of thought inline,
wrapped in ``<think>...</think>``, ahead of the answer proper. Two things go
wrong if that reaches the rest of LANA.

The obvious one is that the user reads it. The damaging one is that
``validate_answer`` extracts *every* number in the text and checks it against
the fact ledger, and a scratchpad is full of figures the model considered and
discarded. Those are precisely the numbers that match no fact, so each one
becomes an "unsupported claim" warning about an answer that may be entirely
correct. The validator is only worth having if a warning means something;
feeding it text the model never intended to show would make it noisy exactly
where it is supposed to be trusted.

Stripping is unconditional and costs nothing: a model that does not emit
these tags is unaffected, because the tags never appear. Only the literal
``<think>`` pair is recognised, which is what Ollama surfaces inline for the
models above; this is deliberately not a general-purpose tag scrubber.
"""
from __future__ import annotations

_OPEN = "<think>"
_CLOSE = "</think>"


def _splittable_tail(buf: str, tag: str) -> int:
    """Index up to which ``buf`` cannot contain the start of a split ``tag``.

    A streamed chunk can end halfway through a tag ("...<thi"), so the tail
    that might still grow into one has to be held back until the next chunk
    arrives rather than passed through.
    """
    for hold in range(min(len(tag) - 1, len(buf)), 0, -1):
        if buf.endswith(tag[:hold]):
            return len(buf) - hold
    return len(buf)


class ReasoningFilter:
    """Remove ``<think>`` blocks from a stream of chunks, tag-split safe.

    Feed each chunk as it arrives and emit whatever comes back; call
    ``flush`` once the stream ends. An unterminated block yields nothing,
    which is the right reading: a model cut off mid-thought never produced an
    answer, and the UI already has a "response cut off" state for that.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out: list[str] = []
        while True:
            if self._inside:
                end = self._buf.find(_CLOSE)
                if end == -1:
                    # Discard everything that cannot be part of a split
                    # closing tag; keep the rest for the next chunk.
                    self._buf = self._buf[_splittable_tail(self._buf, _CLOSE):]
                    break
                self._buf = self._buf[end + len(_CLOSE):]
                self._inside = False
                continue
            start = self._buf.find(_OPEN)
            if start == -1:
                cut = _splittable_tail(self._buf, _OPEN)
                out.append(self._buf[:cut])
                self._buf = self._buf[cut:]
                break
            out.append(self._buf[:start])
            self._buf = self._buf[start + len(_OPEN):]
            self._inside = True
        return "".join(out)

    def flush(self) -> str:
        remainder = "" if self._inside else self._buf
        self._buf = ""
        return remainder


def strip_reasoning(text: str) -> str:
    """Remove ``<think>`` blocks from a complete, non-streamed answer."""
    f = ReasoningFilter()
    return f.feed(text) + f.flush()
