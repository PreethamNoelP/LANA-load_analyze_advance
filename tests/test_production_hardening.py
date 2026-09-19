"""Auth, ownership, rate limiting, coordination and observability.

Each of these was a named gap in the audit — "no rate limiting", "no
per-session ownership", "the token is baked into the JS bundle", "no metrics",
"single process". They are grouped here because they share a shape: none of
them changes what LANA computes, and all of them decide whether a request is
allowed to proceed and what is recorded about it.
"""

from __future__ import annotations

import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
from app import observability as obs
from app.config import config
from backend.coordination import InProcessCoordinator, SqliteCoordinator


@pytest.fixture
def client():
    return TestClient(backend_main.app)


def _csv_bytes(rows: int = 20) -> bytes:
    frame = pd.DataFrame({
        "region": ["north", "south"] * (rows // 2),
        "revenue": [float(i) for i in range(rows)],
    })
    return frame.to_csv(index=False).encode()


def _upload(client: TestClient) -> str:
    response = client.post(
        "/upload", files={"file": ("d.csv", io.BytesIO(_csv_bytes()), "text/csv")}
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


# ── Auth: cookie exchange replaces the bundled token ────────────────────────

@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(config, "auth_token", "test-token-value")
    yield "test-token-value"


def test_no_auth_by_default_leaves_everything_open(client, monkeypatch):
    monkeypatch.setattr(config, "auth_token", "")
    assert client.get("/health").status_code == 200
    assert client.get("/sources").status_code == 200


def test_auth_status_reports_whether_a_token_is_required(client, auth_on):
    body = client.get("/auth/status").json()
    assert body == {"required": True, "authenticated": False}


def test_a_bad_token_is_refused_at_the_exchange(client, auth_on):
    assert client.post("/auth/session", json={"token": "wrong"}).status_code == 401


def test_the_exchange_returns_an_httponly_cookie(client, auth_on):
    """The point of the whole change.

    Previously the token was compiled into the JS bundle, so anyone who could
    load the page could read it. Now the browser trades it once for a cookie
    that page scripts cannot read at all.
    """
    response = client.post("/auth/session", json={"token": auth_on})
    assert response.status_code == 200

    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=strict" in cookie_header


def test_the_cookie_authenticates_subsequent_requests(client, auth_on):
    assert client.get("/sources").status_code == 401
    client.post("/auth/session", json={"token": auth_on})
    # TestClient keeps the cookie jar, so this is the browser's own next call.
    assert client.get("/sources").status_code == 200


def test_a_bearer_header_still_works_for_api_clients(client, auth_on):
    response = client.get("/sources", headers={"Authorization": f"Bearer {auth_on}"})
    assert response.status_code == 200


def test_logout_clears_the_cookie(client, auth_on):
    client.post("/auth/session", json={"token": auth_on})
    assert client.get("/sources").status_code == 200
    client.post("/auth/logout")
    assert client.get("/sources").status_code == 401


def test_health_and_metrics_stay_open_for_infrastructure(client, auth_on):
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200


# ── Session ownership ───────────────────────────────────────────────────────

def test_a_session_created_without_auth_is_unowned(client, monkeypatch):
    """The single-user local default must be completely unchanged."""
    monkeypatch.setattr(config, "auth_token", "")
    sid = _upload(client)
    assert client.get(f"/session/{sid}").status_code == 200


def test_another_principal_cannot_read_your_session(client, auth_on, monkeypatch):
    client.post("/auth/session", json={"token": auth_on})
    sid = _upload(client)
    assert client.get(f"/session/{sid}").status_code == 200

    # A different principal: same server, different identity. Simulated by
    # changing what _principal() derives, which is the one thing ownership
    # compares on.
    monkeypatch.setattr(backend_main, "_principal", lambda request: "tok:someone-else")
    response = client.get(f"/session/{sid}")
    assert response.status_code == 404


def test_the_refusal_is_a_404_not_a_403(client, auth_on, monkeypatch):
    """403 would confirm the id exists, making the endpoint an enumeration oracle."""
    client.post("/auth/session", json={"token": auth_on})
    sid = _upload(client)
    monkeypatch.setattr(backend_main, "_principal", lambda request: "tok:someone-else")

    denied = client.get(f"/session/{sid}")
    missing = client.get("/session/00000000-0000-0000-0000-000000000000")
    assert denied.status_code == missing.status_code == 404
    assert denied.json()["detail"] == missing.json()["detail"]


def test_ownership_covers_every_session_endpoint(client, auth_on, monkeypatch):
    """Ownership is read from a ContextVar precisely so no endpoint can miss it."""
    client.post("/auth/session", json={"token": auth_on})
    sid = _upload(client)
    monkeypatch.setattr(backend_main, "_principal", lambda request: "tok:someone-else")

    for path in (
        f"/session/{sid}", f"/profile/{sid}", f"/lineage/{sid}",
        f"/clean/status/{sid}", f"/recommendations/{sid}",
        f"/export/csv/{sid}", f"/clean/preview/{sid}",
    ):
        assert client.get(path).status_code == 404, f"{path} did not enforce ownership"


# ── Rate limiting ───────────────────────────────────────────────────────────

def test_the_limiter_refuses_a_burst_and_says_when_to_retry(client, monkeypatch):
    monkeypatch.setattr(backend_main, "RATE_CAPACITY", 3.0)
    monkeypatch.setattr(backend_main, "RATE_REFILL_PER_SECOND", 0.01)
    monkeypatch.setattr(backend_main, "_coordinator", InProcessCoordinator())

    statuses = [client.get("/sources").status_code for _ in range(6)]
    assert statuses[:3] == [200, 200, 200]
    assert 429 in statuses

    limited = client.get("/sources")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


def test_health_is_never_rate_limited(client, monkeypatch):
    """A limiter that can starve the healthcheck takes the container down."""
    monkeypatch.setattr(backend_main, "RATE_CAPACITY", 1.0)
    monkeypatch.setattr(backend_main, "RATE_REFILL_PER_SECOND", 0.001)
    monkeypatch.setattr(backend_main, "_coordinator", InProcessCoordinator())

    assert all(client.get("/health").status_code == 200 for _ in range(10))


def test_the_bucket_refills_over_time():
    coordinator = InProcessCoordinator()
    # Capacity 1, refilling fast enough that the next call succeeds.
    assert coordinator.check_rate("k", 1.0, 1000.0).allowed
    decision = coordinator.check_rate("k", 1.0, 1000.0)
    # With a 1000/s refill the bucket is full again within a microsecond, so
    # this asserts refill happens at all rather than a specific timing.
    assert decision.allowed or decision.retry_after_seconds < 0.01


def test_buckets_are_per_principal():
    coordinator = InProcessCoordinator()
    assert coordinator.check_rate("a", 1.0, 0.001).allowed
    assert not coordinator.check_rate("a", 1.0, 0.001).allowed
    # A different key must be unaffected by the first one's exhaustion.
    assert coordinator.check_rate("b", 1.0, 0.001).allowed


# ── Coordination: the multi-worker contract ─────────────────────────────────

@pytest.mark.parametrize("make", [
    lambda tmp: InProcessCoordinator(),
    lambda tmp: SqliteCoordinator(tmp / "coord.db"),
])
def test_slots_are_capped(make, tmp_path):
    coordinator = make(tmp_path)
    assert coordinator.acquire_slot("a", 2)
    assert coordinator.acquire_slot("b", 2)
    assert not coordinator.acquire_slot("c", 2)
    coordinator.release_slot("a")
    assert coordinator.acquire_slot("c", 2)
    coordinator.close()


def test_two_sqlite_coordinators_share_one_cap(tmp_path):
    """The reason this class exists.

    Two coordinators on one file stand in for two uvicorn workers on one
    volume. With the old threading.Semaphore this test is impossible to write,
    because each process simply had its own cap — which is exactly the bug.
    """
    db = tmp_path / "coord.db"
    worker_a = SqliteCoordinator(db)
    worker_b = SqliteCoordinator(db)

    assert worker_a.acquire_slot("a1", 2)
    assert worker_b.acquire_slot("b1", 2)
    # Both slots are now held, one by each "worker".
    assert not worker_a.acquire_slot("a2", 2)
    assert not worker_b.acquire_slot("b2", 2)

    worker_a.release_slot("a1")
    assert worker_b.acquire_slot("b2", 2)

    worker_a.close()
    worker_b.close()


def test_two_sqlite_coordinators_share_one_rate_bucket(tmp_path):
    db = tmp_path / "coord.db"
    worker_a = SqliteCoordinator(db)
    worker_b = SqliteCoordinator(db)

    assert worker_a.check_rate("shared", 2.0, 0.0001).allowed
    assert worker_b.check_rate("shared", 2.0, 0.0001).allowed
    assert not worker_a.check_rate("shared", 2.0, 0.0001).allowed

    worker_a.close()
    worker_b.close()


def test_an_expired_lease_is_reclaimed(tmp_path, monkeypatch):
    """A worker that dies mid-request must not hold a slot forever."""
    import backend.coordination as coordination

    monkeypatch.setattr(coordination, "LEASE_TTL_SECONDS", -1.0)
    coordinator = SqliteCoordinator(tmp_path / "coord.db")
    assert coordinator.acquire_slot("dead-worker", 1)
    # The lease was born already expired, so the next caller reaps it.
    assert coordinator.acquire_slot("live-worker", 1)
    coordinator.close()


def test_releasing_an_unheld_slot_is_harmless(tmp_path):
    coordinator = SqliteCoordinator(tmp_path / "coord.db")
    coordinator.release_slot("never-held")
    coordinator.close()


# ── Session store read-through, the other half of multi-worker ──────────────

def test_a_second_store_reads_a_session_the_first_one_created(tmp_path):
    """Two stores on one directory stand in for two workers on one volume."""
    from backend.session_store import SessionStore

    frame = pd.DataFrame({"a": [1, 2, 3]})
    worker_a = SessionStore(persist_dir=tmp_path)
    session = worker_a.create("sid-1", "d.csv", frame, owner="tok:abc")
    worker_a.persist(session)

    worker_b = SessionStore(persist_dir=tmp_path)
    # Evict it from B's startup cache so the next get() is a genuine miss.
    worker_b._sessions.clear()

    restored = worker_b.get("sid-1")
    assert restored is not None
    assert restored.owner == "tok:abc"
    pd.testing.assert_frame_equal(restored.raw, frame)


def test_a_read_through_miss_returns_none_without_persistence(tmp_path):
    from backend.session_store import SessionStore

    store = SessionStore(persist_dir=None)
    assert store.get("never-existed") is None


def test_read_through_does_not_duplicate_a_live_session(tmp_path):
    """Two Session objects for one id would mean two caches and two locks."""
    from backend.session_store import SessionStore

    store = SessionStore(persist_dir=tmp_path)
    session = store.create("sid-2", "d.csv", pd.DataFrame({"a": [1]}))
    store.persist(session)
    assert store.get("sid-2") is store.get("sid-2") is session


# ── Observability ───────────────────────────────────────────────────────────

def test_metrics_render_in_prometheus_format(client):
    client.get("/health")
    body = client.get("/metrics").text
    assert "# TYPE lana_http_requests_total counter" in body
    assert "# TYPE lana_http_request_seconds histogram" in body
    assert "lana_http_request_seconds_bucket{" in body


def test_metrics_never_leak_dataset_content(client):
    """/metrics is unauthenticated, so it must carry no data, ever."""
    sid = _upload(client)
    client.get(f"/profile/{sid}")
    body = client.get("/metrics").text
    for leaked in ("revenue", "region", "north", "d.csv", sid):
        assert leaked not in body


def test_path_labels_are_collapsed_so_cardinality_stays_bounded(client):
    sid = _upload(client)
    client.get(f"/session/{sid}")
    body = client.get("/metrics").text
    assert 'path="/session/{id}"' in body
    assert sid not in body


def test_every_response_carries_a_request_id(client):
    response = client.get("/health")
    assert response.headers["X-Request-ID"]


def test_a_supplied_request_id_is_honoured(client):
    """Lets a reverse proxy's correlation id flow through rather than being replaced."""
    response = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"


def test_json_logs_carry_the_request_id(caplog, monkeypatch):
    import logging

    formatter = obs.JsonFormatter()
    token = obs.request_id_var.set("req-42")
    try:
        record = logging.LogRecord(
            "lana", logging.INFO, __file__, 1, "something happened", None, None
        )
        record.session_id = "s-1"
        payload = json.loads(formatter.format(record))
    finally:
        obs.request_id_var.reset(token)

    assert payload["request_id"] == "req-42"
    assert payload["session_id"] == "s-1"
    assert payload["message"] == "something happened"


def test_histogram_buckets_are_cumulative():
    histogram = obs.Histogram("t_seconds", "test", buckets=(0.1, 1.0, 10.0))
    for value in (0.05, 0.5, 5.0):
        histogram.observe(value)
    rendered = "\n".join(histogram.render())
    assert 't_seconds_bucket{le="0.1"} 1' in rendered
    assert 't_seconds_bucket{le="1"} 2' in rendered
    assert 't_seconds_bucket{le="10"} 3' in rendered
    assert "t_seconds_count 3" in rendered


def test_label_values_are_escaped():
    counter = obs.Counter("t_total", "test")
    counter.inc(path='/a"b')
    assert '\\"' in "\n".join(counter.render())
