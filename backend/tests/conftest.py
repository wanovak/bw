import os

os.environ.update(
    LLM_MODE="mock",
    JWT_SECRET="test-jwt-secret-0123456789abcdef",
    PARENT_ID_HMAC_SECRET="test-hmac-secret-0123456789abcdef",
    PII_ENCRYPTION_KEY="test-pii-key-0123456789abcdef",
    DEFAULT_OPERATOR_EMAIL="admin@littleacorns.test",
    DEFAULT_OPERATOR_PASSWORD="OperatorPass1!",
    SEED_DEMO_DATA="false",
)

import pytest
from fastapi.testclient import TestClient

from app import llm


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        app.state.test_client = c
        yield c


def new_client() -> TestClient:
    from app.main import app

    return TestClient(app)


@pytest.fixture
def operator(client):
    c = new_client()
    r = c.post("/api/auth/login", json={"email": "admin@littleacorns.test", "password": "OperatorPass1!"})
    assert r.status_code == 200, r.text
    return c


_counter = {"n": 0}


@pytest.fixture
def make_parent(client):
    def _make(name="Pat Parent"):
        _counter["n"] += 1
        c = new_client()
        r = c.post("/api/auth/signup", json={"name": name, "email": f"p{_counter['n']}@example.com", "password": "password123"})
        assert r.status_code == 200, r.text
        return c

    return _make


@pytest.fixture
def parent(make_parent):
    return make_parent()


@pytest.fixture
def fake_llm(monkeypatch):
    """Control exactly what the 'LLM' returns. Usage: fake_llm(answerable=True, confidence=0.97, ...)"""

    def _set(**kw):
        def fake(question, docs):
            res = llm.LLMResult(**{"answerable": True, "answer": "Fake answer.", "confidence": 0.97,
                                   "source_ids": [docs[0].id] if docs else [], "category": "Hours", **kw})
            return res

        monkeypatch.setattr(llm, "answer", fake)

    return _set


def ask(c, text, **kw):
    r = c.post("/api/parent/questions", json={"text": text, **kw})
    assert r.status_code == 200, r.text
    return r.json()
