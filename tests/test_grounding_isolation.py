"""One question's executed facts must not leak into the next one's evidence.

The grounded context is cached per session version, because building it means
profiling every column and running a correlation scan. The executed-SQL path
then needs its query's figures in the fact list so the validator can credit
them — and the way that was done extended the cached list *in place*.

That turned a per-question fact into a permanent one. The consequence is the
worst failure this codebase has: a number the current answer's evidence does
not contain could collide with a leftover fact from an earlier, unrelated
question and be shown to the user marked "verified against the data". A
warning that never fires costs nothing; a green tick on a fabricated figure
costs the user the reason they trusted LANA at all.

These tests pin the boundary at both levels — the helper that builds the
per-request view, and the API endpoints that use it.
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
from app.analysis.sql_engine import DUCKDB_AVAILABLE

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


CSV = (
    b"region,revenue,units\n"
    b"north,100,1\n"
    b"south,200,2\n"
    b"north,300,3\n"
    b"south,400,4\n"
)


@pytest.fixture
def client():
    return TestClient(backend_main.app)


def _upload(client) -> str:
    response = client.post("/upload", files={"file": ("t.csv", CSV, "text/csv")})
    assert response.status_code == 200
    return response.json()["session_id"]


# ── The helper itself ────────────────────────────────────────────────────────

def test_the_cached_context_is_never_the_object_that_gets_sql_facts():
    from app.analysis.sql_engine import SqlResult
    from app.llm.context import Fact, GroundedContext
    from app.llm.sql_answer import SqlGroundedAnswer

    cached = GroundedContext(text="facts", facts=[Fact("row count", 4.0)])
    sql_answer = SqlGroundedAnswer(
        answer="…",
        result=SqlResult(sql="SELECT 1", columns=["n"], rows=[[1]],
                         row_count=1, truncated=False, elapsed_ms=0.1),
        facts=[Fact("avg_revenue (executed SQL)", 250.0, provenance="executed_sql")],
    )

    view = backend_main._with_sql_facts(cached, sql_answer)

    assert view is not cached
    assert view.facts is not cached.facts
    # The per-request view sees both; the cached object is untouched, so the
    # next question starts from the ledger alone.
    assert len(view.facts) == 2
    assert len(cached.facts) == 1
    # Everything the validator reads but never writes is shared, not rebuilt.
    assert view.text is cached.text


def test_no_sql_answer_returns_the_cached_context_unchanged():
    from app.llm.context import GroundedContext

    cached = GroundedContext(text="facts")
    assert backend_main._with_sql_facts(cached, None) is cached


# ── End to end through the API ───────────────────────────────────────────────

class _SqlProvider:
    """Plans one query per question, then answers from its result."""

    def __init__(self, *sql: str):
        self.sql = list(sql)

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        if "=== QUERY RESULT" in prompt:
            return "Reported."
        return self.sql.pop(0)

    def answer_question(self, question: str, context: str) -> str:
        return "Reported."


@pytest.mark.skipif(not DUCKDB_AVAILABLE, reason="duckdb is not installed")
def test_a_second_question_does_not_inherit_the_first_question_s_facts(
    client, monkeypatch
):
    sid = _upload(client)
    monkeypatch.setattr(backend_main, "SQL_GROUNDING_ENABLED", True)
    monkeypatch.setattr(
        backend_main, "get_provider",
        lambda: _SqlProvider(
            'SELECT SUM("revenue") AS total_revenue FROM dataset',
            'SELECT COUNT(*) AS n FROM dataset',
        ),
    )

    assert client.post(
        "/query", json={"session_id": sid, "question": "total revenue?"}
    ).status_code == 200

    # The second question's own evidence is a single count. If the first
    # question's 1000.0 were still in the shared fact list, an answer quoting
    # it would validate — which is exactly the collision being ruled out.
    assert client.post(
        "/query", json={"session_id": sid, "question": "how many rows?"}
    ).status_code == 200

    session = backend_main._store.get(sid)
    cached = session.context(lambda df, profiles: None)
    assert all(f.provenance != "executed_sql" for f in cached.facts), (
        "executed-SQL facts were written into the session's cached context, so "
        "every later question on this dataset is validated against them"
    )


@pytest.mark.skipif(not DUCKDB_AVAILABLE, reason="duckdb is not installed")
def test_the_streaming_path_leaves_the_cached_context_clean(client, monkeypatch):
    sid = _upload(client)
    monkeypatch.setattr(backend_main, "SQL_GROUNDING_ENABLED", True)
    monkeypatch.setattr(
        backend_main, "get_provider",
        lambda: _SqlProvider('SELECT SUM("revenue") AS total_revenue FROM dataset'),
    )

    with client.stream(
        "POST", "/query/stream",
        json={"session_id": sid, "question": "total revenue?"},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    # The client is told which path answered on both branches, not only on the
    # ledger one — otherwise "which grounding produced this?" is something the
    # UI has to infer from the absence of an event.
    assert '"grounding": "sql"' in body
    assert '"sql":' in body

    session = backend_main._store.get(sid)
    cached = session.context(lambda df, profiles: None)
    assert all(f.provenance != "executed_sql" for f in cached.facts)


def test_repeated_questions_do_not_grow_the_cached_fact_list(client, monkeypatch):
    """Even on the ledger path, the fact count is a property of the data."""
    sid = _upload(client)
    monkeypatch.setattr(backend_main, "SQL_GROUNDING_ENABLED", False)

    class _Ledger:
        def answer_question(self, question: str, context: str) -> str:
            return "The dataset has 4 rows."

    monkeypatch.setattr(backend_main, "get_provider", lambda: _Ledger())

    counts = []
    for _ in range(3):
        client.post("/query", json={"session_id": sid, "question": "rows?"})
        counts.append(len(backend_main._store.get(sid).context(lambda d, p: None).facts))

    assert len(set(counts)) == 1, f"fact list grew across questions: {counts}"


def test_the_dataframe_is_not_the_thing_being_compared():
    """Guards the fixture's own assumption, so a silent parse change is caught."""
    frame = pd.read_csv(pd.io.common.BytesIO(CSV))
    assert frame["revenue"].sum() == 1000
