import json

import pytest
from typer.testing import CliRunner

from aegis.cli import app
from aegis.github_setup import check_github_setup


@pytest.fixture
def setup_env(monkeypatch, tmp_path):
    path = tmp_path / "roles.json"
    path.write_text(json.dumps({"123": {"role": "admin", "workspace_ids": []}}))
    values = {
        "CLIENT_ID": "private-client",
        "CLIENT_SECRET": "private-secret",
        "CALLBACK": "http://127.0.0.1:8766/api/auth/github/callback",
        "ROLES_FILE": str(path),
    }
    for key, value in values.items():
        monkeypatch.setenv("AEGIS_GITHUB_" + key, value)
    return path


def test_check_reports_presence_without_leaking_values(setup_env):
    result = CliRunner().invoke(app, ["check-github"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["valid_members"] == 1
    assert report["live_login_verified"] is False
    for private in ("private-secret", "private-client", str(setup_env)):
        assert private not in result.stdout


def test_missing_secret_is_actionable(monkeypatch, setup_env):
    monkeypatch.delenv("AEGIS_GITHUB_CLIENT_SECRET")
    result = CliRunner().invoke(app, ["check-github"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["missing_settings"] == ["AEGIS_GITHUB_CLIENT_SECRET"]


@pytest.mark.parametrize(
    "mapping",
    [
        [],
        {},
        {"0123": {"role": "admin"}},
        {"123": {"role": "reader"}},
        {"123": {"role": "owner-secret"}},
    ],
)
def test_invalid_roles_are_rejected_without_echoing_input(setup_env, mapping):
    setup_env.write_text(json.dumps(mapping))
    result = check_github_setup()
    assert result["status"] == "configuration_required"
    assert result["valid_members"] == 0
    assert "owner-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "callback",
    [
        "http://example.com/api/auth/github/callback",
        "http://127.0.0.1:bad/api/auth/github/callback",
        "http://127.0.0.1/api/auth/github/callback",
    ],
)
def test_callback_port_and_host_checked(monkeypatch, setup_env, callback):
    monkeypatch.setenv("AEGIS_GITHUB_CALLBACK", callback)
    assert check_github_setup()["status"] == "configuration_required"


def test_oversized_and_unreadable_roles(setup_env):
    setup_env.write_bytes(b" " * 1_048_577)
    assert check_github_setup()["status"] == "configuration_required"
    setup_env.unlink()
    assert check_github_setup()["status"] == "configuration_required"
