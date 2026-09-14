"""The optional LANA_AUTH_TOKEN gate — off by default, every endpoint open."""

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
from backend.main import app


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def auth_client(monkeypatch):
    monkeypatch.setattr(backend_main.config, "auth_token", "s3cret")
    return TestClient(app, raise_server_exceptions=False)


def test_health_is_exempt_from_the_auth_token(auth_client):
    assert auth_client.get("/health").status_code == 200


def test_request_without_token_is_rejected(auth_client):
    r = auth_client.get("/models")
    assert r.status_code == 401


def test_request_with_wrong_token_is_rejected(auth_client):
    r = auth_client.get("/models", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_request_with_correct_token_is_accepted(auth_client):
    r = auth_client.get("/models", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_auth_disabled_by_default_leaves_every_endpoint_open(client):
    # `client` fixture: no token configured anywhere.
    assert client.get("/models").status_code == 200
