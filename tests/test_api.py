"""HTTP-level tests for the FastAPI app (no external services)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_root(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert "Agentic AI Recruiter API" in body.get("message", "")


def test_openapi_available(client: TestClient) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec.get("openapi")
    assert "/api/auth/needs-setup" in spec.get("paths", {})


def test_needs_setup(client: TestClient) -> None:
    r = client.get("/api/auth/needs-setup")
    assert r.status_code == 200
    data = r.json()
    assert "needs_setup" in data
    assert isinstance(data["needs_setup"], bool)


def test_roles_requires_auth(client: TestClient) -> None:
    r = client.get("/api/roles")
    assert r.status_code == 401
