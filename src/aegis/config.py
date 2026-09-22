"""Explicit YAML < environment precedence; no implicit .env loading."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError


class ConfigurationError(ValueError):
    """Public error whose text never includes configuration values."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    data_dir: Path = Path(".aegis")
    database_url: SecretStr | None = None


def load_settings(
    path: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> Settings:
    values: dict[str, object] = {}
    if path is not None:
        try:
            if path.stat().st_size > 65536:
                raise ConfigurationError("Configuration exceeds 64 KiB limit")
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
            raise ConfigurationError("Cannot read valid YAML configuration") from None
        if raw is not None:
            if not isinstance(raw, dict) or any(not isinstance(k, str) for k in raw):
                raise ConfigurationError("Configuration must be a mapping with string keys")
            values.update(raw)
    env = os.environ if environ is None else environ
    for field in Settings.model_fields:
        key = f"AEGIS_{field.upper()}"
        if key in env:
            values[field] = env[key]
    try:
        return Settings.model_validate(values)
    except ValidationError:
        raise ConfigurationError(
            "Invalid configuration; check supported fields and types"
        ) from None
