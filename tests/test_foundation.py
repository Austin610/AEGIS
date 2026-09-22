import io
import json
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from aegis.cli import app
from aegis.config import ConfigurationError, load_settings
from aegis.logging import configure_logging, run_context


def test_defaults_and_precedence(tmp_path: Path) -> None:
    assert load_settings(environ={}).log_level == "INFO"
    config = tmp_path / "config.yaml"
    config.write_text("log_level: DEBUG\nenvironment: test\n", encoding="utf-8")
    settings = load_settings(config, environ={"AEGIS_LOG_LEVEL": "ERROR"})
    assert settings.log_level == "ERROR"
    assert settings.environment == "test"


@pytest.mark.parametrize(
    "content", ["[a, b]", "x: [", "log_level: secret-canary", "x: secret-canary", "1: x"]
)
def test_invalid_configuration_is_sanitized(tmp_path: Path, content: str) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        load_settings(config, environ={})
    assert "secret-canary" not in str(error.value)


def test_missing_and_large_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(tmp_path / "missing", environ={})
    config = tmp_path / "large.yaml"
    config.write_text("x" * 65537, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="64 KiB"):
        load_settings(config, environ={})


def test_secret_repr() -> None:
    settings = load_settings(environ={"AEGIS_DATABASE_URL": "secret-canary"})
    assert "secret-canary" not in repr(settings)


def test_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in ("ENVIRONMENT", "LOG_LEVEL", "DATA_DIR", "DATABASE_URL"):
        monkeypatch.delenv(f"AEGIS_{key}", raising=False)
    runner = CliRunner()
    assert runner.invoke(app, ["--version"]).stdout.strip() == "AEGIS 0.1.0"
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "ok"
    result = runner.invoke(app, ["doctor", "--config", str(tmp_path / "missing")])
    assert result.exit_code == 2
    monkeypatch.setenv("AEGIS_LOG_LEVEL", "secret-canary")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 2
    assert "secret-canary" not in result.output


def test_logging_correlation_and_secret_omission() -> None:
    output = io.StringIO()
    logger = configure_logging(stream=output)
    first, second = uuid4(), uuid4()
    with run_context(first):
        logger.info("doctor.completed")
        with run_context(second):
            logger.error("secret-canary %s", "password", extra={"token": "secret-canary"})
        logger.info("doctor.completed")
    logger.info("doctor.completed")
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["run_id"] for r in rows] == [str(first), str(second), str(first), None]
    assert "secret-canary" not in output.getvalue()
    assert "password" not in output.getvalue()


def test_logging_setup_does_not_duplicate_events() -> None:
    output = io.StringIO()
    configure_logging(stream=output)
    logger = configure_logging(stream=output)
    logger.info("doctor.completed")
    assert len(output.getvalue().splitlines()) == 1


def test_exception_body_is_not_logged_and_context_resets() -> None:
    output = io.StringIO()
    logger = configure_logging(stream=output)
    with pytest.raises(ValueError), run_context(uuid4()):
        try:
            raise ValueError("secret-canary")
        except ValueError:
            logger.exception("configuration.invalid")
            raise
    logger.info("doctor.completed")
    assert "secret-canary" not in output.getvalue()
    assert json.loads(output.getvalue().splitlines()[-1])["run_id"] is None
