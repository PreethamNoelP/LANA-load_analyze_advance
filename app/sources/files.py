"""File-backed sources: CSV, Excel and JSON on local disk.

This wraps the existing ``app.data.ingest`` rather than reimplementing it. The
upload path keeps its own route — it has streaming, spooling and pre-parse
admission control that only make sense for an HTTP body — and this connector
exists so a file already on disk reaches the same pipeline through the same
abstraction as every other source, which is what makes the conformance suite
in ``tests/test_sources.py`` able to cover all of them uniformly.

Why there is a path policy
--------------------------
This connector reads a path the *caller* supplies, on the *server's* disk.
On the laptop LANA is built for that is the entire feature: your machine,
your files, and a path box is more convenient than a file picker for
``~/exports/orders.csv``.

The moment LANA is reachable by someone else — the configuration the project
explicitly supports by setting ``LANA_AUTH_TOKEN`` — the same feature is an
arbitrary server-side file read. ``POST /sources/load`` with
``{"kind": "file", "target": "/srv/other-tenant/export.csv"}`` returns that
file's contents as a dataset, and nothing in the request looks unusual. The
extension allowlist is not a defence: CSV and JSON are exactly the formats
interesting files are in.

So the policy is decided by which deployment this is, and it is explicit
rather than inferred at the point of use:

* **No token set** (single-user local, the default) — unrestricted. Reading
  your own disk on your own machine is the feature, and narrowing it would
  make the common case worse to defend against a threat that is not present.
* **``LANA_FILE_SOURCE_ROOTS`` set** — reads are confined to those
  directories, symlinks and ``..`` resolved first so a path cannot climb out
  of one. This is the setting for a shared deployment that genuinely wants a
  server-side data drop.
* **Token set, no roots** — the connector reports itself unavailable, naming
  the variable to set. Refusing to guess is the point: the alternative is
  either silently exposing the filesystem or silently removing a feature
  someone was using.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from .base import (
    DEFAULT_ROW_LIMIT,
    ConnectionTest,
    DataSource,
    FetchResult,
    SourceCapabilities,
    SourceConfigError,
    SourceConnectionError,
    SourceRefused,
    SourceUnavailable,
    normalize_frame,
)

SUPPORTED_SUFFIXES = (".csv", ".xlsx", ".json")


def allowed_roots() -> tuple[Path, ...]:
    """Directories a file source may read from, from the environment.

    Read at call time rather than import time, so a test (and an operator
    reloading configuration) can change it without re-importing the module —
    the same reason ``app.sources.security.private_urls_allowed`` does.
    """
    raw = os.getenv("LANA_FILE_SOURCE_ROOTS", "")
    roots = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser().resolve())
    return tuple(roots)


def _is_shared_deployment() -> bool:
    """Whether someone other than the operator can reach this instance.

    ``LANA_AUTH_TOKEN`` being set is the project's own signal for "this is
    not just me on localhost" — SECURITY.md says so and the README says so —
    so it is the right thing to key this on rather than inventing a second,
    separate switch that could disagree with the first.
    """
    return bool(os.getenv("LANA_AUTH_TOKEN", "").strip())


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


class FileSource(DataSource):
    kind = "file"
    description = "A CSV, Excel (.xlsx) or JSON file on this machine."
    capabilities = SourceCapabilities(
        lists_entities=False,
        needs_entity=False,
        accepts_query=False,
        uses_secret=False,
        target_label="File path",
        target_placeholder="/data/orders.csv",
    )

    def validate_spec(self) -> None:
        path = self._require(
            self.spec.target, "File path", "give the path to a .csv, .xlsx or .json file"
        )
        # Checked before the extension, so ``available_sources()`` — which
        # probes each connector with a dummy spec — sees the deployment
        # policy rather than a complaint about the dummy's file type, and the
        # UI can grey the connector out with a reason instead of offering a
        # path box that will always be refused.
        roots = allowed_roots()
        if not roots and _is_shared_deployment():
            raise SourceUnavailable(
                "Reading files from the server's disk is disabled because this "
                "instance requires an auth token, which means more than one "
                "person can reach it — and this connector would let any of "
                "them read any CSV or JSON on the host. Set "
                "LANA_FILE_SOURCE_ROOTS to the directories that may be read "
                "(comma-separated) to enable it. Uploading a file works "
                "regardless."
            )

        suffix = Path(path).suffix.lower()
        if suffix == ".xls":
            raise SourceConfigError(
                "The legacy '.xls' format is not supported. Open it in Excel or "
                "LibreOffice and save it as '.xlsx' (or export it as CSV)."
            )
        if suffix not in SUPPORTED_SUFFIXES:
            raise SourceConfigError(
                f"Unsupported file type '{suffix or 'none'}'. Use "
                f"{', '.join(SUPPORTED_SUFFIXES)}."
            )
        # Resolves the path once here so a malformed spec fails at
        # construction, before any I/O. `_path` re-checks on every access
        # rather than trusting this one, because a symlink can be repointed
        # between validation and the read.
        self._checked_path()

    def _checked_path(self) -> Path:
        """The target as an absolute path, confined to the allowed roots.

        ``resolve()`` first, always: it collapses ``..`` and follows symlinks,
        which is what turns "is this path under the root" from a string
        comparison anyone can defeat with ``/data/../etc/passwd.csv`` into a
        statement about the file that will actually be opened.
        """
        path = Path(self.spec.target).expanduser().resolve()
        roots = allowed_roots()
        if not roots:
            # Single-user local install — see the module docstring. The
            # shared-deployment case was already refused in validate_spec.
            return path
        if not _within(path, roots):
            raise SourceRefused(
                f"'{path.name}' is outside the directories this instance may "
                f"read from. Allowed: "
                f"{', '.join(str(r) for r in roots)}. Upload the file instead, "
                f"or ask the operator to extend LANA_FILE_SOURCE_ROOTS."
            )
        return path

    @property
    def _path(self) -> Path:
        return self._checked_path()

    def test_connection(self) -> ConnectionTest:
        path = self._path
        if not path.exists():
            return ConnectionTest(False, f"No file at '{path}'.")
        if not path.is_file():
            return ConnectionTest(False, f"'{path}' is not a file.")
        size_mb = path.stat().st_size / 1024 ** 2
        return ConnectionTest(
            True, f"Found '{path.name}' ({size_mb:,.1f} MB).", entities=[path.name]
        )

    def fetch(self, *, limit: int = DEFAULT_ROW_LIMIT) -> FetchResult:
        path = self._path
        if not path.exists():
            raise SourceConnectionError(f"No file at '{path}'.")

        suffix = path.suffix.lower()
        try:
            if suffix == ".csv":
                # nrows is pushed into the reader, so a capped read of a huge
                # CSV does not parse the whole file first.
                frame = pd.read_csv(path, nrows=limit)
            elif suffix == ".xlsx":
                frame = pd.read_excel(path, nrows=limit)
            else:
                frame = pd.read_json(path)
        except ValueError as exc:
            raise SourceConnectionError(f"Could not parse '{path.name}': {exc}") from exc
        except OSError as exc:
            raise SourceConnectionError(f"Could not read '{path.name}': {exc}") from exc

        truncated = len(frame) >= limit
        if len(frame) > limit:
            frame = frame.head(limit)

        return FetchResult(
            frame=normalize_frame(frame),
            label=path.name,
            row_limit_applied=truncated,
            notes=[f"Read {len(frame):,} rows from {path.name}."],
        )
