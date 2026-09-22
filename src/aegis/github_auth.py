"""Opt-in GitHub OAuth login with PKCE and explicit local role assignments."""

import base64
import hashlib
import http.client
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from aegis.access import NewKey, Principal


class GitHubLogin:
    def __init__(self) -> None:
        self.client_id = os.environ.get("AEGIS_GITHUB_CLIENT_ID", "")
        self.client_secret = os.environ.get("AEGIS_GITHUB_CLIENT_SECRET", "")
        self.callback = os.environ.get("AEGIS_GITHUB_CALLBACK", "")
        self.mapping_file = os.environ.get("AEGIS_GITHUB_ROLES_FILE", "")
        self.pending: dict[str, tuple[float, str]] = {}
        if self.enabled:
            url = urlsplit(self.callback)
            if (
                url.scheme != "http"
                or url.hostname not in {"127.0.0.1", "localhost"}
                or url.path != "/api/auth/github/callback"
                or url.query
                or url.fragment
                or url.username
                or url.password
            ):
                raise ValueError("GitHub callback must be the local AEGIS callback endpoint")

    @property
    def enabled(self) -> bool:
        return all((self.client_id, self.client_secret, self.callback, self.mapping_file))

    def principal(self, identity: str) -> Principal | None:
        if not self.enabled or not identity.startswith("github:"):
            return None
        try:
            path = Path(self.mapping_file)
            if path.stat().st_size > 1_048_576:
                return None
            mapping = json.loads(path.read_text(encoding="utf-8"))
            entry = mapping.get(identity.removeprefix("github:"))
            if entry is None:
                return None
            role = NewKey.model_validate({"label": "GitHub member", **entry})
            return Principal(id=identity, role=role.role, workspace_ids=role.workspace_ids)
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def begin(self) -> tuple[str, str]:
        if not self.enabled:
            raise ValueError("GitHub login is not configured")
        now = time.monotonic()
        self.pending = {key: value for key, value in self.pending.items() if value[0] > now}
        if len(self.pending) >= 100:
            raise ValueError("Too many pending sign-ins")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        self.pending[state] = (now + 600, verifier)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        return state, "https://github.com/login/oauth/authorize?" + urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.callback,
                "scope": "read:user",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

    @staticmethod
    def request(
        host: str, method: str, path: str, *, body: str | None = None, token: str | None = None
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "AEGIS-local-workbench"}
        if body:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token:
            headers["Authorization"] = "Bearer " + token
        connection = http.client.HTTPSConnection(host, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(1_048_577)
            if response.status != 200 or len(raw) > 1_048_576:
                raise ValueError("GitHub authentication failed")
            result: dict[str, Any] = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Invalid GitHub response")
            return result
        except (OSError, http.client.HTTPException, ValueError):
            raise ValueError("GitHub authentication failed") from None
        finally:
            connection.close()

    def finish(self, code: str, state: str, cookie: str) -> Principal:
        if not self.enabled or not state or not secrets.compare_digest(state, cookie):
            raise ValueError("Sign-in state mismatch")
        pending = self.pending.pop(state, None)
        if pending is None or pending[0] < time.monotonic():
            raise ValueError("Sign-in expired or already used")
        result = self.request(
            "github.com",
            "POST",
            "/login/oauth/access_token",
            body=urlencode(
                {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": self.callback,
                    "code_verifier": pending[1],
                }
            ),
        )
        token = result.get("access_token")
        if not isinstance(token, str) or not token or len(token) > 10000:
            raise ValueError("GitHub did not return an access token")
        profile = self.request("api.github.com", "GET", "/user", token=token)
        identity = profile.get("id")
        if type(identity) is not int or identity <= 0:
            raise ValueError("GitHub identity missing")
        principal = self.principal("github:" + str(identity))
        if principal is None:
            raise ValueError("GitHub account has no AEGIS role assignment")
        # Provider access tokens are never persisted or returned to the browser.
        return principal
