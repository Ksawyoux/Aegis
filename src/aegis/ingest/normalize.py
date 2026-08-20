"""Service attribution and canonical log-level normalization."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias

from aegis.ingest.textnorm import normalize_repo, normalize_service

ResolutionReason: TypeAlias = Literal["no_service_match", "ambiguous_service"]

_REPLICASET_POD_RE = re.compile(r"-[A-Za-z0-9]{10}-[A-Za-z0-9]{5}$")
_STATEFULSET_POD_RE = re.compile(r"-\d+$")

_LEVEL_MAP = {
    "trace": "debug",
    "debug": "debug",
    "info": "info",
    "notice": "info",
    "information": "info",
    "warn": "warning",
    "warning": "warning",
    "err": "error",
    "error": "error",
    "severe": "error",
    "fatal": "fatal",
    "critical": "fatal",
    "crit": "fatal",
    "panic": "fatal",
    "emerg": "fatal",
    "alert": "fatal",
}


class ServiceRecord(Protocol):
    """The subset of :class:`~aegis.db.models.Service` used by this registry."""

    name: str
    repo: str | None
    log_keys: list[str]
    k8s_names: list[str]
    infra_tags: dict[str, Any]


class AmbiguousServiceMapping(ValueError):
    """Raised when registry configuration cannot yield deterministic attribution."""


@dataclass(frozen=True)
class ServiceResolution:
    """The service selected for an event, or its unresolved-event reason."""

    service: ServiceRecord | None
    reason: ResolutionReason | None = None

    def __post_init__(self) -> None:
        if self.service is None and self.reason is None:
            raise ValueError("unresolved service resolution requires a reason")
        if self.service is not None and self.reason is not None:
            raise ValueError("resolved service resolution cannot have a reason")

    @property
    def resolved(self) -> bool:
        """Whether a service was selected."""
        return self.service is not None


@dataclass(frozen=True)
class _RegisteredService:
    record: ServiceRecord
    name: str
    repo: str | None
    log_keys: frozenset[str]
    k8s_names: frozenset[str]
    infra_tags: Mapping[str, Any]


class ServiceRegistry:
    """An immutable, deterministic lookup registry for ORM ``Service`` records.

    ``load`` also accepts dataclasses or other objects exposing the ``Service``
    columns used here.  Resolution always returns the original input object.
    """

    def __init__(self, services: Iterable[ServiceRecord]) -> None:
        registered = tuple(_register(service) for service in services)
        self._services = registered
        self._names = _unique_index(registered, "name")
        self._log_keys = _multi_value_index(registered, "log_keys", "log_key")
        self._k8s_names = _multi_value_index(registered, "k8s_names", "k8s_name")
        self._repos = _unique_optional_index(registered, "repo", "repo")
        _validate_tag_ambiguity(registered)

    @classmethod
    def load(cls, services: Iterable[ServiceRecord]) -> ServiceRegistry:
        """Build a registry and reject ambiguous configured identifiers."""
        return cls(services)

    def resolve_service(
        self,
        record: Mapping[str, Any] | None = None,
        /,
        *,
        name: str | None = None,
        log_key: str | None = None,
        k8s_name: str | None = None,
        repo: str | None = None,
        infra_tags: Mapping[str, Any] | None = None,
    ) -> ServiceResolution:
        """Resolve an event according to the fixed identity-strength order.

        A positional mapping is accepted as a convenience for parsed event
        attributes. Explicit keyword arguments take precedence over its values.
        """
        if record is not None:
            name = name if name is not None else record.get("name")
            log_key = log_key if log_key is not None else record.get("log_key")
            k8s_name = k8s_name if k8s_name is not None else record.get("k8s_name")
            repo = repo if repo is not None else record.get("repo")
            infra_tags = infra_tags if infra_tags is not None else record.get("infra_tags")

        # A present name is an assertion of identity: it deliberately does not
        # fall through to any weaker signal when it is unknown.
        if name is not None:
            try:
                normalized_name = normalize_service(name)
            except (TypeError, ValueError):
                return ServiceResolution(service=None, reason="no_service_match")
            return _resolution_for(self._names.get(normalized_name))

        if log_key is not None:
            return _resolution_for(self._log_keys.get(log_key))

        if k8s_name is not None:
            for candidate in _pod_name_candidates(k8s_name):
                match = self._k8s_names.get(candidate)
                if match is not None:
                    return _resolution_for(match)

        if repo is not None:
            try:
                normalized_repo = normalize_repo(repo)
            except (TypeError, ValueError):
                normalized_repo = None
            if normalized_repo is not None:
                return _resolution_for(self._repos.get(normalized_repo))

        if infra_tags:
            matches = tuple(
                service
                for service in self._services
                if service.infra_tags
                and all(infra_tags.get(key) == value for key, value in service.infra_tags.items())
            )
            if len(matches) == 1:
                return _resolution_for(matches[0])
            if len(matches) > 1:
                return ServiceResolution(service=None, reason="ambiguous_service")

        return ServiceResolution(service=None, reason="no_service_match")


def canonical_level(level: str | None = None, status_code: int | None = None) -> str:
    """Return one of the five persisted log levels.

    A recognized explicit level takes precedence. Only an absent level derives
    from a valid HTTP status; unknown explicit levels safely normalize to info.
    """
    if isinstance(level, str) and level.strip():
        return _LEVEL_MAP.get(level.strip().casefold(), "info")

    if _is_valid_status(status_code):
        assert status_code is not None
        if status_code >= 500:
            return "error"
        if status_code >= 400:
            return "warning"
    return "info"


def status_class(status_code: int | None) -> str:
    """Return the valid HTTP status class, or ``none`` for absent/anomalous input."""
    if not _is_valid_status(status_code):
        return "none"
    assert status_code is not None
    return f"{status_code // 100}xx"


def _register(record: ServiceRecord) -> _RegisteredService:
    name = normalize_service(record.name)
    repo = normalize_repo(record.repo) if record.repo is not None else None
    return _RegisteredService(
        record=record,
        name=name,
        repo=repo,
        log_keys=frozenset(record.log_keys),
        k8s_names=frozenset(record.k8s_names),
        infra_tags=dict(record.infra_tags),
    )


def _unique_index(
    services: Iterable[_RegisteredService], attribute: Literal["name"]
) -> dict[str, _RegisteredService]:
    index: dict[str, _RegisteredService] = {}
    for service in services:
        value = getattr(service, attribute)
        _add_unique(index, value, service, attribute)
    return index


def _multi_value_index(
    services: Iterable[_RegisteredService],
    attribute: Literal["log_keys", "k8s_names"],
    label: str,
) -> dict[str, _RegisteredService]:
    index: dict[str, _RegisteredService] = {}
    for service in services:
        for value in getattr(service, attribute):
            _add_unique(index, value, service, label)
    return index


def _unique_optional_index(
    services: Iterable[_RegisteredService],
    attribute: Literal["repo"],
    label: str,
) -> dict[str, _RegisteredService]:
    index: dict[str, _RegisteredService] = {}
    for service in services:
        value = getattr(service, attribute)
        if value is not None:
            _add_unique(index, value, service, label)
    return index


def _add_unique(
    index: dict[str, _RegisteredService],
    value: str,
    service: _RegisteredService,
    label: str,
) -> None:
    prior = index.get(value)
    if prior is not None and prior is not service:
        raise AmbiguousServiceMapping(
            f"{label} {value!r} is configured for both {prior.name!r} and {service.name!r}"
        )
    index[value] = service


def _validate_tag_ambiguity(services: tuple[_RegisteredService, ...]) -> None:
    tagged = tuple(service for service in services if service.infra_tags)
    for index, first in enumerate(tagged):
        for second in tagged[index + 1 :]:
            shared_keys = first.infra_tags.keys() & second.infra_tags.keys()
            if all(first.infra_tags[key] == second.infra_tags[key] for key in shared_keys):
                raise AmbiguousServiceMapping(
                    "infra_tags for "
                    f"{first.name!r} and {second.name!r} can match the same event"
                )


def _pod_name_candidates(name: str) -> Iterable[str]:
    """Yield the literal pod name before each supported Kubernetes suffix removal."""
    candidate = name
    yield candidate
    while True:
        stripped = _REPLICASET_POD_RE.sub("", candidate)
        if stripped == candidate:
            stripped = _STATEFULSET_POD_RE.sub("", candidate)
        if stripped == candidate:
            return
        candidate = stripped
        yield candidate


def _resolution_for(service: _RegisteredService | None) -> ServiceResolution:
    if service is None:
        return ServiceResolution(service=None, reason="no_service_match")
    return ServiceResolution(service=service.record)


def _is_valid_status(status_code: int | None) -> bool:
    return (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 100 <= status_code <= 599
    )
