"""
Configure process env before importing `main` so the FastAPI app uses a throwaway SQLite file
and full DB init (users table for auth) runs like production.
"""
from __future__ import annotations

import os
import tempfile

import pytest


def _configure_env() -> str:
    fd, path = tempfile.mkstemp(suffix="-pytest.db")
    os.close(fd)
    path_norm = path.replace("\\", "/")
    os.environ["DATABASE_URL"] = f"sqlite:///{path_norm}"
    os.environ["USE_DATABASE"] = "true"
    os.environ["JWT_SECRET"] = os.environ.get("JWT_SECRET") or "pytest-jwt-secret"
    os.environ["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "pytest-secret-key"
    os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY") or "sk-pytest-placeholder"
    os.environ.setdefault("ENVIRONMENT", "development")
    return path


_DB_PATH = _configure_env()

from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    try:
        os.remove(_DB_PATH)
    except OSError:
        pass
