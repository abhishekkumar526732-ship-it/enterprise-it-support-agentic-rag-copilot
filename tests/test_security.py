"""
Tests for Authentication, JWT tokens, and RBAC roles.
"""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_login_invalid_credentials():
    response = client.post(
        "/api/auth/token",
        json={"username": "nonexistent_user", "password": "wrongpassword"},
    )
    assert response.status_code == 401


def test_login_success_default_user():
    response = client.post(
        "/api/auth/token",
        json={"username": "user", "password": "user123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_protected_chat_requires_auth():
    response = client.post("/api/chat", json={"question": "How do I connect to VPN?"})
    assert response.status_code == 401


def test_admin_ingest_requires_auth():
    response = client.post("/api/ingest")
    assert response.status_code == 401
