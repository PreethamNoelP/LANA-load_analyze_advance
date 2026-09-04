"""Host resource detection and size-adaptive processing budgets.

LANA runs on the user's own machine, which means the right limits are not
constants — an 8 GB laptop and a 64 GB workstation should not be handed the
same 2 GB session budget. This module answers two questions the rest of the
codebase needs:

* **How much room do we actually have?** Total and currently-available RAM,
  and the CPU count, read from the OS with the standard library only. No
  third-party dependency is added for something this small, and every probe
  degrades to a conservative constant rather than raising.
* **How hard should we work on a frame this size?** A single place that
  decides when to sample, how many columns to detail, and how much of a
  column a plot needs — so the profiler, the chart renderer and the context
  builder all scale the same way instead of each inventing a threshold.

Two rules hold for everything here:

1. **Conservative when unsure.** A failed probe reports less memory than the
   machine probably has. Under-promising degrades performance; over-promising
   invites the OOM kill that this module exists to prevent.
2. **Sampling is never silent.** Every budget that reduces work returns the
   reason alongside the number, so the caller can state what it did. A
   statistic computed on a sample and presented as exact is precisely the
   failure LANA is built to avoid.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# Fallbacks used when the OS refuses to say. Deliberately small: a machine we
# cannot measure is treated as a modest laptop.
_ASSUMED_TOTAL_BYTES = 4 * 1024 ** 3
_ASSUMED_AVAILABLE_FRACTION = 0.35

# Share of *total* RAM that all session data together may occupy.
#
# Deliberately total and not available: a budget is a policy, and a policy
# derived from a transient measurement is not stable. Reading "available" here
# made the upload ceiling swing between 81 MB and 671 MB on the same laptop
# depending on how many browser tabs happened to be open when the process
# started — so the same file would be accepted or refused for reasons the user
# could neither see nor predict, and a restart could change the answer.
#
# Current pressure is handled where it belongs, at admission time, by
# `can_admit()` below. Fixed budget for predictability; live check for safety.
#
# The remaining 75% covers the interpreter (~240 MB measured), per-request
# working copies, and everything else the user is running — a browser and an
# editor are not optional on a laptop.
_SESSION_BUDGET_FRACTION = 0.25
_MIN_SESSION_BUDGET_BYTES = 256 * 1024 ** 2
_MAX_SESSION_BUDGET_BYTES = 8 * 1024 ** 3

# Measured on this codebase: a 93 MB CSV upload peaked at ~5.7x the file size
# in process RSS (body buffering + parser working set + profiling temporaries).
# Admission control uses this to reject a file that cannot be parsed safely
# *before* pandas materialises it, rather than discovering the problem by
# being killed.
UPLOAD_PEAK_MULTIPLIER = 6.0


def _total_bytes() -> int:
    """Physical RAM, or a conservative guess."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        else:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and page_size > 0:
                return int(pages) * int(page_size)
    except Exception:
        pass
    return _ASSUMED_TOTAL_BYTES


def _available_bytes(total: int) -> int:
    """RAM the OS says is free for use right now, or a fraction of total.

    ``MemAvailable`` on Linux and ``ullAvailPhys`` on Windows are the honest
    figures — they account for reclaimable cache, which ``MemFree`` does not.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys)
        elif sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
    except Exception:
        pass
    return int(total * _ASSUMED_AVAILABLE_FRACTION)


@dataclass(frozen=True)
class HostResources:
    """What this machine can spare, measured once at import."""

    total_bytes: int
    available_bytes: int
    cpu_count: int
    measured: bool          # False when a probe failed and defaults were used

    @property
    def total_mb(self) -> float:
        return round(self.total_bytes / 1024 ** 2, 1)

    @property
    def available_mb(self) -> float:
        return round(self.available_bytes / 1024 ** 2, 1)

    def session_budget_bytes(self) -> int:
        """How many bytes of session data this host should hold at once.

        A stable figure derived from installed RAM, so the same file gets the
        same answer on the same machine every time. Momentary pressure is the
        job of :func:`can_admit`.
        """
        budget = int(self.total_bytes * _SESSION_BUDGET_FRACTION)
        return max(_MIN_SESSION_BUDGET_BYTES, min(_MAX_SESSION_BUDGET_BYTES, budget))

    def upload_limit_bytes(self) -> int:
        """Largest single file this host can parse without risking the process.

        The parse transiently needs several times the file size (see
        ``UPLOAD_PEAK_MULTIPLIER``), so the ceiling is the session budget
        divided by that factor — not the session budget itself.
        """
        return max(8 * 1024 ** 2, int(self.session_budget_bytes() / UPLOAD_PEAK_MULTIPLIER))

    def to_dict(self) -> dict:
        return {
            "total_mb": self.total_mb,
            "available_mb": self.available_mb,
            "cpu_count": self.cpu_count,
            "measured": self.measured,
            "session_budget_mb": round(self.session_budget_bytes() / 1024 ** 2, 1),
            "upload_limit_mb": round(self.upload_limit_bytes() / 1024 ** 2, 1),
        }


def detect() -> HostResources:
    """Probe the host. Cheap enough to call again if something changes."""
    total = _total_bytes()
    available = _available_bytes(total)
    measured = total != _ASSUMED_TOTAL_BYTES
    # Available can legitimately exceed a *guessed* total; keep it coherent.
    available = min(available, total)
    return HostResources(
        total_bytes=total,
        available_bytes=available,
        cpu_count=os.cpu_count() or 1,
        measured=measured,
    )


HOST = detect()


# Share of *currently free* RAM a single new dataset may claim. The budget in
# HOST was fixed at startup; by the time someone uploads, the user may have
# opened a browser and an IDE. Re-probing means LANA yields to the machine's
# real state instead of holding a reservation it made minutes ago.
_ADMISSION_FRACTION = 0.5


def can_admit(projected_bytes: int) -> tuple[bool, int]:
    """Whether ``projected_bytes`` can be admitted given RAM free *right now*.

    Returns ``(ok, free_bytes)``. The caller reports the free figure, because
    "not enough memory" without a number is not something a user can act on.

    This is a second gate, not a replacement for the fixed budget: the budget
    stops LANA from filling a quiet machine, and this stops it from tipping
    over a busy one.
    """
    free = _available_bytes(_total_bytes())
    return projected_bytes <= int(free * _ADMISSION_FRACTION), free


# ── Size-adaptive work budgets ───────────────────────────────────────────────
# One place decides how hard to work on a frame of a given size. Thresholds are
# row counts because that is what every cost here scales with.

# Below this, everything runs on the full frame: the work is cheap enough that
# sampling would add complexity and remove exactness for no gain.
EXACT_ROW_LIMIT = 250_000

# Above this, operations whose answers converge (correlation coefficients,
# distribution shape, KDE curves) run on a fixed deterministic sample.
SAMPLE_ROWS = 200_000

# A plot cannot show more points than it has pixels. Past this, scatter and
# line charts are drawing on top of themselves and getting slower doing it.
PLOT_POINT_LIMIT = 50_000

# Seed for every sample LANA takes. Fixed so that the same upload always
# produces the same numbers — a correlation that changes between refreshes is
# worse than one computed slightly more cheaply.
SAMPLE_SEED = 0


@dataclass(frozen=True)
class WorkPlan:
    """How to process a frame of a given size, and what to disclose about it."""

    rows: int
    sample_rows: int | None      # None means use every row
    reason: str | None           # why sampling was applied, for the caller to state

    @property
    def sampled(self) -> bool:
        return self.sample_rows is not None

    def note(self) -> str | None:
        """Disclosure text, or None when the result is exact."""
        if not self.sampled:
            return None
        return (
            f"computed on a fixed random sample of {self.sample_rows:,} rows "
            f"out of {self.rows:,} ({self.reason})"
        )


def plan_for(rows: int, *, limit: int = SAMPLE_ROWS, purpose: str = "cost") -> WorkPlan:
    """Decide whether an operation over ``rows`` rows should sample.

    ``limit`` lets a caller be stricter than the default — a scatter plot
    needs far fewer points than a correlation coefficient does.
    """
    if rows <= max(limit, EXACT_ROW_LIMIT if purpose == "cost" else limit):
        return WorkPlan(rows=rows, sample_rows=None, reason=None)
    return WorkPlan(rows=rows, sample_rows=limit, reason=purpose)
