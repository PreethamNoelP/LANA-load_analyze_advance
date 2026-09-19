"""The connector registry.

Adding a data source to LANA is meant to be one subclass and one ``register``
call, with no edits anywhere else — not in the API routes, not in the session
store, not in the frontend. That is the property this module exists to
provide, and ``tests/test_sources.py`` enforces it by driving every registered
connector through the same conformance suite and by asserting the API's
``/sources`` listing is generated from the registry rather than hardcoded.

A connector whose driver is not installed still registers. It reports itself
as unavailable with an install hint, which is strictly more useful than
vanishing from the list and leaving the user to guess why MongoDB is not
offered. ``app.export`` already established this pattern with its optional
PDF/DOCX dependencies.
"""

from __future__ import annotations

from typing import Any

from .base import (
    DEFAULT_ROW_LIMIT,
    PREVIEW_ROWS,
    ConnectionTest,
    DataSource,
    FetchResult,
    SourceCapabilities,
    SourceConfigError,
    SourceConnectionError,
    SourceError,
    SourceRefused,
    SourceSpec,
    SourceUnavailable,
    normalize_frame,
    redact,
)
from .files import FileSource
from .mongo import MongoSource
from .rest import RestSource
from .sql_db import SqlSource

__all__ = [
    "DEFAULT_ROW_LIMIT",
    "PREVIEW_ROWS",
    "ConnectionTest",
    "DataSource",
    "FetchResult",
    "SourceCapabilities",
    "SourceConfigError",
    "SourceConnectionError",
    "SourceError",
    "SourceRefused",
    "SourceSpec",
    "SourceUnavailable",
    "available_sources",
    "build_source",
    "normalize_frame",
    "redact",
    "register",
    "registry",
]

_REGISTRY: dict[str, type[DataSource]] = {}


def register(source_class: type[DataSource]) -> type[DataSource]:
    """Add a connector to the registry. Usable as a decorator."""
    if not source_class.kind:
        raise ValueError(f"{source_class.__name__} must define a 'kind'.")
    if source_class.kind in _REGISTRY:
        raise ValueError(f"A source of kind '{source_class.kind}' is already registered.")
    _REGISTRY[source_class.kind] = source_class
    return source_class


def registry() -> dict[str, type[DataSource]]:
    return dict(_REGISTRY)


def _driver_available(source_class: type[DataSource]) -> tuple[bool, str]:
    """Whether this connector's optional dependency is importable.

    Asked by constructing nothing and importing nothing new: each module sets
    an ``*_AVAILABLE`` flag at import time, and the class exposes its own
    unavailability through ``validate_spec``. Rather than duplicate that
    knowledge here, a probe spec is built and the resulting error is read.
    """
    try:
        source_class(SourceSpec(kind=source_class.kind, target="probe"))
        return True, ""
    except SourceUnavailable as exc:
        return False, str(exc)
    except SourceError:
        # A config error means the driver loaded and rejected the probe spec,
        # which is exactly what "available" looks like from here.
        return True, ""
    except Exception:
        return True, ""


def available_sources() -> list[dict[str, Any]]:
    """Every registered connector, described for the UI's source picker.

    Generated, never hardcoded — this is what the ``/sources`` endpoint
    returns, so a newly registered connector appears in the interface without
    a frontend change.
    """
    listing: list[dict[str, Any]] = []
    for kind, source_class in sorted(_REGISTRY.items()):
        ok, reason = _driver_available(source_class)
        listing.append({
            "kind": kind,
            "description": source_class.description,
            "available": ok,
            "unavailable_reason": reason,
            "capabilities": source_class.capabilities.to_dict(),
        })
    return listing


def build_source(spec: SourceSpec) -> DataSource:
    """Construct the connector for a spec, or explain why that is impossible."""
    source_class = _REGISTRY.get(spec.kind)
    if source_class is None:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise SourceConfigError(
            f"Unknown source type '{spec.kind}'. Available types: {known}."
        )
    return source_class(spec)


register(FileSource)
register(SqlSource)
register(MongoSource)
register(RestSource)
