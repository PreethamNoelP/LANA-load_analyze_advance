"""Structured logging, request correlation and metrics.

The audit's finding was blunt and correct: five ``logger.warning`` calls in the
whole codebase, no metrics, no tracing, no request IDs — "no way to answer why
was yesterday slow". This module is the answer, and it is deliberately
dependency-free.

Why no ``structlog`` / ``prometheus_client``
--------------------------------------------
The same reasoning ``app/resources.py`` already applies to RAM probing: a
JSON formatter is forty lines and a counter is a dict with a lock. Adding two
dependencies to a locally-installed, offline-first tool — each with its own
transitive tree and CVE surface — buys convenience, not capability. The
exposition format below is Prometheus text format 0.0.4, so every standard
scraper, Grafana Agent and OpenTelemetry collector reads it without knowing
LANA rolled its own registry.

What is deliberately *not* here: distributed tracing. A span that never
leaves one process is a log line with extra ceremony, and LANA makes no
outbound calls worth tracing except to the model, whose latency is already a
histogram. If LANA ever grows a service boundary, OpenTelemetry becomes worth
its weight; today it would be cargo cult.

Request correlation
-------------------
``request_id`` is a ``ContextVar``, so it follows a request through
``run_in_threadpool`` without being threaded manually through every function
signature. Every log record emitted while handling a request carries it, which
is what turns "an error happened" into "this user's upload of this file
failed, and here are the eleven other lines from the same request".
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# Set per request by the API middleware; empty outside one (startup, CLI use).
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
# The authenticated principal, where there is one. Used to attribute rate
# limiting and session ownership in logs without logging the token itself.
principal_var: ContextVar[str] = ContextVar("principal", default="")


# ── Structured logging ──────────────────────────────────────────────────────

# Attributes LogRecord always carries. Anything outside this set was passed by
# the caller as an `extra=` field and belongs in the JSON output.
_STANDARD_RECORD_FIELDS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with request correlation attached.

    Line-delimited JSON because that is what every log shipper ingests without
    configuration, and because a multi-line traceback inside a single JSON
    string survives shipping intact where a bare traceback does not.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            ) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        principal = principal_var.get()
        if principal:
            payload["principal"] = principal

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = _loggable(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def _loggable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_loggable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _loggable(v) for k, v in value.items()}
    return str(value)


class PlainFormatter(logging.Formatter):
    """Human-readable output for a developer running uvicorn in a terminal.

    JSON logs are for machines. A contributor running ``uvicorn --reload``
    should not have to pipe through ``jq`` to read a warning, so the format is
    chosen by ``LANA_LOG_FORMAT`` and defaults to plain outside containers.
    """

    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_var.get()
        prefix = f"[{request_id[:8]}] " if request_id else ""
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {prefix}{record.name}: {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging() -> None:
    """Install the configured formatter on the root logger, once.

    Idempotent: uvicorn's reloader imports the app module more than once, and
    stacking handlers produces duplicate lines that look like a retry bug.
    """
    level = os.getenv("LANA_LOG_LEVEL", "INFO").upper()
    fmt = os.getenv("LANA_LOG_FORMAT", "plain").lower()

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_lana_handler", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else PlainFormatter())
    handler._lana_handler = True  # type: ignore[attr-defined]

    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))

    # uvicorn installs its own handlers; without this every request is logged
    # twice, once in each format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True


# ── Metrics ─────────────────────────────────────────────────────────────────

def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    inner = ",".join(
        f'{k}="{_escape_label(v)}"' for k, v in sorted(labels.items())
    )
    return "{" + inner + "}"


def _escape_label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@dataclass
class _Series:
    """One labelled time series within a metric."""

    labels: dict[str, str]
    value: float = 0.0
    # Histogram state, unused by counters and gauges.
    count: int = 0
    total: float = 0.0
    buckets: dict[float, int] = field(default_factory=dict)


class Metric:
    """Base for the three metric types. Thread-safe; every method takes the lock."""

    def __init__(self, name: str, help_text: str, metric_type: str,
                 buckets: tuple[float, ...] | None = None) -> None:
        self.name = name
        self.help_text = help_text
        self.type = metric_type
        self.buckets = buckets or ()
        self._series: dict[tuple[tuple[str, str], ...], _Series] = {}
        self._lock = threading.Lock()

    def _key(self, labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((str(k), str(v)) for k, v in labels.items()))

    def _get(self, labels: dict[str, str]) -> _Series:
        key = self._key(labels)
        series = self._series.get(key)
        if series is None:
            series = _Series(labels=dict(labels))
            if self.buckets:
                series.buckets = dict.fromkeys(self.buckets, 0)
            self._series[key] = series
        return series

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} {self.type}"]
        with self._lock:
            for series in self._series.values():
                lines.extend(self._render_series(series))
        return lines

    def _render_series(self, series: _Series) -> list[str]:
        return [f"{self.name}{_format_labels(series.labels)} {series.value:g}"]


class Counter(Metric):
    """Monotonically increasing total."""

    def __init__(self, name: str, help_text: str) -> None:
        super().__init__(name, help_text, "counter")

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        with self._lock:
            self._get(labels).value += amount


class Gauge(Metric):
    """A value that goes up and down."""

    def __init__(self, name: str, help_text: str) -> None:
        super().__init__(name, help_text, "gauge")

    def set(self, value: float, **labels: str) -> None:
        with self._lock:
            self._get(labels).value = value

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        with self._lock:
            self._get(labels).value += amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)


# Bucket edges in seconds. Chosen for what LANA actually does rather than a
# library default: sub-second for metadata reads, a long tail out to two
# minutes because a local model answering on CPU legitimately takes 30s+ and a
# histogram that tops out at 10s would report every LLM call as "+Inf".
DEFAULT_BUCKETS = (0.005, 0.025, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)


class Histogram(Metric):
    """Latency distribution, exported as Prometheus cumulative buckets."""

    def __init__(self, name: str, help_text: str,
                 buckets: tuple[float, ...] = DEFAULT_BUCKETS) -> None:
        super().__init__(name, help_text, "histogram", buckets)

    def observe(self, seconds: float, **labels: str) -> None:
        with self._lock:
            series = self._get(labels)
            series.count += 1
            series.total += seconds
            # Only the narrowest bucket the value falls into is incremented.
            # Prometheus buckets are cumulative in the *exposition*, and
            # `_render_series` does that accumulation — incrementing every
            # matching bucket here as well counted each observation once per
            # bucket it fell into, so a 0.5s sample showed up three times in
            # `le="10"`. Storage stays non-cumulative; rendering cumulates.
            for edge in self.buckets:
                if seconds <= edge:
                    series.buckets[edge] += 1
                    break

    @contextmanager
    def time(self, **labels: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe(time.perf_counter() - started, **labels)

    def _render_series(self, series: _Series) -> list[str]:
        lines = []
        cumulative = 0
        for edge in self.buckets:
            cumulative += series.buckets.get(edge, 0)
            labels = {**series.labels, "le": _format_float(edge)}
            lines.append(f"{self.name}_bucket{_format_labels(labels)} {cumulative}")
        lines.append(
            f"{self.name}_bucket{_format_labels({**series.labels, 'le': '+Inf'})} "
            f"{series.count}"
        )
        lines.append(f"{self.name}_sum{_format_labels(series.labels)} {series.total:g}")
        lines.append(f"{self.name}_count{_format_labels(series.labels)} {series.count}")
        return lines


def _format_float(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


class Registry:
    """Holds every metric and renders the exposition format."""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}
        self._lock = threading.Lock()

    def register(self, metric: Metric) -> Metric:
        with self._lock:
            if metric.name in self._metrics:
                return self._metrics[metric.name]
            self._metrics[metric.name] = metric
            return metric

    def render(self) -> str:
        with self._lock:
            metrics = list(self._metrics.values())
        lines: list[str] = []
        for metric in sorted(metrics, key=lambda m: m.name):
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Drop every sample. Tests only — never called by the application."""
        with self._lock:
            for metric in self._metrics.values():
                with metric._lock:
                    metric._series.clear()


REGISTRY = Registry()


def counter(name: str, help_text: str) -> Counter:
    return REGISTRY.register(Counter(name, help_text))  # type: ignore[return-value]


def gauge(name: str, help_text: str) -> Gauge:
    return REGISTRY.register(Gauge(name, help_text))  # type: ignore[return-value]


def histogram(name: str, help_text: str,
              buckets: tuple[float, ...] = DEFAULT_BUCKETS) -> Histogram:
    return REGISTRY.register(Histogram(name, help_text, buckets))  # type: ignore[return-value]


# ── LANA's metrics ──────────────────────────────────────────────────────────
# Defined here rather than at their call sites so the full set is readable in
# one place and cannot be silently duplicated under two spellings.

http_requests = counter(
    "lana_http_requests_total", "HTTP requests by method, path template and status."
)
http_latency = histogram(
    "lana_http_request_seconds", "HTTP request latency by method and path template."
)
http_in_flight = gauge(
    "lana_http_requests_in_flight", "HTTP requests currently being handled."
)

uploads = counter(
    "lana_uploads_total", "Dataset loads by source kind and outcome."
)
upload_rows = histogram(
    "lana_upload_rows", "Rows per loaded dataset.",
    buckets=(100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000),
)

llm_requests = counter(
    "lana_llm_requests_total", "LLM calls by path (sql|ledger) and outcome."
)
llm_latency = histogram(
    "lana_llm_seconds", "LLM end-to-end answer latency by path."
)
llm_rejected = counter(
    "lana_llm_rejected_total", "Requests refused before reaching the model, by reason."
)

sql_queries = counter(
    "lana_sql_queries_total", "Generated SQL queries by outcome."
)
sql_latency = histogram(
    "lana_sql_seconds", "Executed query latency.",
    buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 20.0),
)

validation_claims = counter(
    "lana_validation_claims_total",
    "Numeric claims checked, by verdict and provenance.",
)
validation_answers = counter(
    "lana_validation_answers_total", "Validated answers by whether they were flagged."
)

sessions_active = gauge("lana_sessions_active", "Sessions currently resident.")
sessions_bytes = gauge("lana_sessions_bytes", "Resident bytes across all sessions.")

rate_limited = counter(
    "lana_rate_limited_total", "Requests rejected by the rate limiter, by scope."
)
