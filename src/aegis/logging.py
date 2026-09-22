"""Allowlisted JSON events: arbitrary messages and exception bodies are omitted."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TextIO
from uuid import UUID

_run_id: ContextVar[str | None] = ContextVar("aegis_run_id", default=None)
_events = frozenset({"doctor.completed", "configuration.invalid"})


@contextmanager
def run_context(run_id: UUID) -> Iterator[None]:
    token = _run_id.set(str(run_id))
    try:
        yield
    finally:
        _run_id.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = record.msg if isinstance(record.msg, str) and record.msg in _events else "event"
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "event": event,
                "run_id": _run_id.get(),
            }
        )


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> logging.Logger:
    logger = logging.getLogger("aegis")
    logger.setLevel(level)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger
