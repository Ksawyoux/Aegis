"""Run-scoped tool-result capture and deterministic trace recording."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    """One JSON-safe event emitted while an investigation is running."""

    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TraceSink(Protocol):
    """Destination for investigation trace events."""

    def emit(self, event: TraceEvent) -> None:
        """Persist or otherwise receive an emitted trace event."""


@dataclass
class InMemorySink:
    """A trace sink for v0.1 callers and tests."""

    events: list[TraceEvent] = field(default_factory=list)

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)


class RunContext:
    """Per-investigation state, supplied with a caller-owned run identifier."""

    def __init__(self, run_id: str, sink: TraceSink) -> None:
        self.run_id = run_id
        self._sink = sink
        self._captured_cites: set[str] = set()
        self._events: list[TraceEvent] = []

    @property
    def captured_cites(self) -> set[str]:
        """Return citations observed in tool responses for this run only."""
        return set(self._captured_cites)

    def emit(self, event: TraceEvent) -> None:
        """Emit a trace event to the injected sink."""
        self._events.append(event)
        self._sink.emit(event)

    def capture_tool_result(self, tool: str, args: dict[str, Any], result: BaseModel) -> None:
        """Capture named citation fields from a Pydantic tool response and trace it."""
        self._captured_cites.update(_citations_in_model(result))
        self.emit(
            TraceEvent(
                kind="tool_result",
                payload=_sorted_json(
                    {
                        "tool": tool,
                        "args": args,
                        "result": result.model_dump(mode="json"),
                    }
                ),
            )
        )

    def to_json(self) -> dict[str, Any]:
        """Return the deterministic, JSON-safe trace representation for this run."""
        events = [event.model_dump(mode="json") for event in self._events]
        # _sorted_json is recursive over arbitrary JSON, so it returns Any; the
        # top-level value here is a dict by construction.
        return cast("dict[str, Any]", _sorted_json({"run_id": self.run_id, "trace": events}))


_CITATION_FIELD_NAMES = frozenset({"cite", "source_cites", "baseline_cites", "resolution_cite"})


def _citations_in_model(model: BaseModel) -> set[str]:
    """Harvest named citation fields while recursively walking Pydantic values."""
    captured: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                field_value = getattr(value, name)
                if name in _CITATION_FIELD_NAMES:
                    captured.update(_citation_strings(field_value))
                visit(field_value)
        elif isinstance(value, list | tuple):
            for item in value:
                visit(item)

    visit(model)
    return captured


def _citation_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list | tuple):
        return {item for item in value if isinstance(item, str)}
    return set()


def _sorted_json(value: Any) -> Any:
    """Recursively sort mapping keys after Pydantic has produced JSON-safe data."""
    if isinstance(value, Mapping):
        return {key: _sorted_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sorted_json(item) for item in value]
    return value


__all__ = ["InMemorySink", "RunContext", "TraceEvent", "TraceSink"]
