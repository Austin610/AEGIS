"""Core protocol: no concrete adapter imports and no shell execution."""

from typing import Literal, Protocol

from pydantic import Field

from aegis.assurance import Fixture
from aegis.policy import Model


class AdapterOutput(Model):
    kind: str
    data: dict[str, object] = Field(default_factory=dict)
    fixture: Fixture | None = None


class Adapter(Protocol):
    id: str
    name: str
    version: str
    modes: tuple[str, ...]
    capability: Literal["fixture.evaluate", "evidence.import"]

    async def healthcheck(self) -> bool: ...
    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput: ...


class Registry:
    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {}

    def register(self, adapter: Adapter) -> None:
        if adapter.id in self._adapters:
            raise ValueError("Duplicate adapter identifier")
        self._adapters[adapter.id] = adapter

    def get(self, identity: str) -> Adapter:
        if identity not in self._adapters:
            raise ValueError("Unknown adapter")
        return self._adapters[identity]

    def manifest(self) -> list[dict[str, object]]:
        return [
            {
                "id": a.id,
                "name": a.name,
                "version": a.version,
                "modes": a.modes,
                "capability": a.capability,
                "execution": "offline",
            }
            for a in self._adapters.values()
        ]
