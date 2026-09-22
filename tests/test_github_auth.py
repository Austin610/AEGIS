import json
from urllib.parse import parse_qs, urlsplit

import pytest

from aegis.github_auth import GitHubLogin


def configured(monkeypatch, tmp_path):
    roles = tmp_path / "roles.json"
    roles.write_text(json.dumps({"123": {"role": "admin", "workspace_ids": []}}))
    monkeypatch.setenv("AEGIS_GITHUB_CLIENT_ID", "test-client")
    monkeypatch.setenv("AEGIS_GITHUB_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("AEGIS_GITHUB_CALLBACK", "http://127.0.0.1:8766/api/auth/github/callback")
    monkeypatch.setenv("AEGIS_GITHUB_ROLES_FILE", str(roles))
    return GitHubLogin(), roles


def test_github_pkce_one_time_state_allowlist_and_revocation(monkeypatch, tmp_path):
    provider, roles = configured(monkeypatch, tmp_path)
    calls = []

    def request(host, method, path, **kwargs):
        calls.append((host, path, kwargs))
        return {"access_token": "test-provider-token"} if host == "github.com" else {"id": 123}

    monkeypatch.setattr(provider, "request", request)
    state, url = provider.begin()
    assert parse_qs(urlsplit(url).query)["code_challenge_method"] == ["S256"]
    with pytest.raises(ValueError):
        provider.finish("code", state, "wrong-cookie")
    assert not calls
    principal = provider.finish("code", state, state)
    assert principal.id == "github:123"
    assert "code_verifier=" in calls[0][2]["body"]
    with pytest.raises(ValueError):
        provider.finish("code", state, state)
    roles.write_text("{}")
    assert provider.principal(principal.id) is None
    state, _ = provider.begin()
    with pytest.raises(ValueError):
        provider.finish("code", state, state)


def test_github_disabled_and_callback_is_fixed(monkeypatch, tmp_path):
    for key in ("CLIENT_ID", "CLIENT_SECRET", "CALLBACK", "ROLES_FILE"):
        monkeypatch.delenv("AEGIS_GITHUB_" + key, raising=False)
    assert not GitHubLogin().enabled
    with pytest.raises(ValueError):
        GitHubLogin().begin()
    configured(monkeypatch, tmp_path)
    monkeypatch.setenv("AEGIS_GITHUB_CALLBACK", "http://untrusted.example/callback")
    with pytest.raises(ValueError):
        GitHubLogin()


def test_github_callback_session_and_role_removal(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from aegis.api import create_app

    _, roles = configured(monkeypatch, tmp_path)

    def request(host, method, path, **kwargs):
        return {"access_token": "test-provider-token"} if host == "github.com" else {"id": 123}

    monkeypatch.setattr(GitHubLogin, "request", staticmethod(request))
    with TestClient(
        create_app(tmp_path / "github.db", "test-owner-token-000000000000000000"),
        base_url="http://127.0.0.1:8766",
    ) as client:
        assert client.get("/api/auth/providers").json()["github"] is True
        response = client.get("/api/auth/github/start", follow_redirects=False)
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        response = client.get(
            "/api/auth/github/callback",
            params={"state": state, "code": "test-code"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert client.get("/api/session").json()["id"] == "github:123"
        assert "test-provider-token" not in str(client.cookies)
        roles.write_text("{}")
        assert client.get("/api/session").status_code == 401
