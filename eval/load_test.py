"""Concurrency benchmark: latency percentiles, memory growth and refusal behaviour.

The audit's finding was that the memory-budget reasoning in
``app/resources.py`` is careful but **untested under contention** — "no
concurrent load test evidenced". This is that test.

What it measures
----------------
* **Latency percentiles** (p50/p95/p99) per endpoint under N concurrent
  clients. Percentiles rather than a mean, because a mean hides exactly the
  tail a user notices, and p95 is the number an SLO is written against.
* **Resident memory growth** across the run, which is what the session
  budget exists to bound. A flat-ish RSS after hundreds of requests is the
  evidence that the per-version caches are actually caches and not a leak.
* **Refusal behaviour under pressure** — whether an over-budget upload is
  cleanly refused with a 413/503 rather than taking the process down. "Does
  it OOM" is not answerable by hoping; it is answerable by asking for more
  than the budget and checking the status code.

Two modes, and the difference is stated because it changes what the numbers mean
------------------------------------------------------------------------------
* **In-process** (default): drives the ASGI app through ``TestClient`` on a
  thread pool. Measures the application — lock contention, cache behaviour,
  pandas work, serialisation — with no network, no uvicorn worker pool and no
  HTTP parsing. Numbers are a *lower bound* on real latency.
* ``--url http://localhost:8000``: drives a real running server over real
  HTTP. Slower, and the only mode whose numbers include the whole stack.

Reporting the in-process figure as if it were end-to-end would be exactly the
kind of overclaim this project documents against, so the mode is printed in
the header of every report and recorded in the JSON.

    python -m eval.load_test --concurrency 16 --requests 400
    python -m eval.load_test --url http://localhost:8000 --concurrency 32
    python -m eval.load_test --json eval/results/load.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Resident memory, without adding a dependency ────────────────────────────

def rss_bytes() -> int:
    """This process's resident set size, or 0 if it cannot be read.

    Same posture as ``app/resources.py``: stdlib only, and a failed probe
    degrades to "unknown" rather than raising. psutil would be one line, but
    it would also be a new runtime dependency for a benchmark script.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _Counters()
            counters.cb = ctypes.sizeof(_Counters)
            # Wrapped in HANDLE explicitly: GetCurrentProcess returns the
            # pseudo-handle -1, and passing it as a bare Python int lets
            # ctypes marshal it as a 32-bit value on 64-bit Windows. The call
            # then fails silently and every reading comes back 0, which is
            # exactly what the first run of this benchmark reported.
            handle = wintypes.HANDLE(ctypes.windll.kernel32.GetCurrentProcess())
            if ctypes.WinDLL("psapi.dll").GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            ):
                return int(counters.WorkingSetSize)
            return 0
        if sys.platform == "darwin":
            import resource

            # macOS reports ru_maxrss in bytes; Linux in kibibytes.
            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        with open("/proc/self/statm") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return 0


# ── Result collection ───────────────────────────────────────────────────────

@dataclass
class EndpointStats:
    name: str
    latencies_ms: list[float] = field(default_factory=list)
    statuses: dict[int, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, elapsed_ms: float, status: int) -> None:
        with self._lock:
            self.latencies_ms.append(elapsed_ms)
            self.statuses[status] = self.statuses.get(status, 0) + 1

    def record_error(self, message: str) -> None:
        with self._lock:
            self.errors.append(message)

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        # Nearest-rank, which for a few hundred samples is both standard and
        # not misleading about precision the sample size cannot support.
        index = min(len(ordered) - 1, max(0, int(round(p / 100 * len(ordered))) - 1))
        return ordered[index]

    @property
    def ok_count(self) -> int:
        return sum(c for s, c in self.statuses.items() if 200 <= s < 300)

    @property
    def error_count(self) -> int:
        return sum(c for s, c in self.statuses.items() if s >= 500) + len(self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n": len(self.latencies_ms),
            "ok": self.ok_count,
            "errors": self.error_count,
            "statuses": {str(k): v for k, v in sorted(self.statuses.items())},
            "p50_ms": round(self.percentile(50), 1),
            "p95_ms": round(self.percentile(95), 1),
            "p99_ms": round(self.percentile(99), 1),
            "max_ms": round(max(self.latencies_ms), 1) if self.latencies_ms else 0.0,
            "mean_ms": round(statistics.fmean(self.latencies_ms), 1)
            if self.latencies_ms else 0.0,
        }


# ── Workload ────────────────────────────────────────────────────────────────

def make_dataset(rows: int, seed: int = 7) -> bytes:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({
        "order_id": np.arange(rows),
        "region": rng.choice(["north", "south", "east", "west"], size=rows),
        "channel": rng.choice(["organic", "paid", "referral"], size=rows),
        "revenue": rng.exponential(220.0, size=rows).round(2),
        "marketing_spend": rng.exponential(45.0, size=rows).round(2),
        "customer_age": rng.integers(18, 70, size=rows),
    })
    return frame.to_csv(index=False).encode()


class Driver:
    """Issues requests, either in-process or over HTTP, behind one interface."""

    def __init__(self, url: str | None) -> None:
        self.url = url.rstrip("/") if url else None
        if self.url:
            import httpx

            self._client = httpx.Client(timeout=120.0)
        else:
            from fastapi.testclient import TestClient

            import backend.main as backend_main

            self._client = TestClient(backend_main.app)

    def request(self, method: str, path: str, **kwargs) -> tuple[int, Any]:
        target = f"{self.url}{path}" if self.url else path
        response = self._client.request(method, target, **kwargs)
        return response.status_code, response

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            close()


def timed(stats: EndpointStats, fn: Callable[[], tuple[int, Any]]) -> Any:
    started = time.perf_counter()
    try:
        status, response = fn()
    except Exception as exc:
        stats.record_error(f"{type(exc).__name__}: {exc}")
        return None
    stats.record((time.perf_counter() - started) * 1000.0, status)
    return response


def run_load(
    driver: Driver,
    *,
    concurrency: int,
    requests_per_endpoint: int,
    rows: int,
) -> dict[str, Any]:
    payload = make_dataset(rows)

    upload_stats = EndpointStats("POST /upload")
    session_ids: list[str] = []
    session_lock = threading.Lock()

    # Phase 1: concurrent uploads. This is the expensive path — parse, dtype
    # optimisation, profiling — and the one that allocates.
    def do_upload() -> None:
        response = timed(upload_stats, lambda: driver.request(
            "POST", "/upload",
            files={"file": ("load.csv", payload, "text/csv")},
        ))
        if response is not None and response.status_code == 200:
            with session_lock:
                session_ids.append(response.json()["session_id"])

    uploads = max(concurrency, 8)
    rss_start = rss_bytes()
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(as_completed([pool.submit(do_upload) for _ in range(uploads)]))

    if not session_ids:
        raise RuntimeError(
            "No uploads succeeded, so there is nothing to measure. "
            f"Statuses: {upload_stats.statuses}, errors: {upload_stats.errors[:3]}"
        )

    rss_after_uploads = rss_bytes()

    # Phase 2: mixed read load across the endpoints a session actually serves.
    endpoints: list[tuple[str, Callable[[str], tuple[int, Any]]]] = [
        ("GET /session", lambda sid: driver.request("GET", f"/session/{sid}")),
        ("GET /profile", lambda sid: driver.request("GET", f"/profile/{sid}")),
        ("GET /correlation", lambda sid: driver.request("GET", f"/correlation/{sid}")),
        ("GET /recommendations", lambda sid: driver.request("GET", f"/recommendations/{sid}")),
        ("GET /stats", lambda sid: driver.request(
            "GET", f"/stats/{sid}", params={"column": "revenue"})),
        ("POST /chart", lambda sid: driver.request("POST", "/chart", json={
            "session_id": sid, "chart_type": "Histogram", "column": "revenue"})),
        ("GET /clean/preview", lambda sid: driver.request("GET", f"/clean/preview/{sid}")),
        ("GET /export/csv", lambda sid: driver.request("GET", f"/export/csv/{sid}")),
        ("GET /health", lambda _sid: driver.request("GET", "/health")),
    ]

    stats_by_name = {name: EndpointStats(name) for name, _ in endpoints}
    jobs = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for index in range(requests_per_endpoint):
            for name, call in endpoints:
                sid = session_ids[index % len(session_ids)]
                jobs.append(pool.submit(
                    timed, stats_by_name[name], lambda c=call, s=sid: c(s)
                ))
        list(as_completed(jobs))

    elapsed = time.perf_counter() - started
    rss_end = rss_bytes()

    total_requests = uploads + sum(
        len(s.latencies_ms) for s in stats_by_name.values()
    )

    return {
        "mode": "http" if driver.url else "in-process (ASGI, no network)",
        "concurrency": concurrency,
        "dataset_rows": rows,
        "uploads": upload_stats.to_dict(),
        "endpoints": [s.to_dict() for s in stats_by_name.values()],
        "totals": {
            "requests": total_requests,
            "wall_seconds": round(elapsed, 2),
            "throughput_rps": round(total_requests / elapsed, 1) if elapsed else 0.0,
            "errors": upload_stats.error_count
            + sum(s.error_count for s in stats_by_name.values()),
        },
        "memory": {
            # Whose memory this is, stated explicitly. In --url mode the
            # benchmark and the server are different processes, so these
            # numbers describe the *client* and say nothing at all about the
            # server's footprint. Reporting them under a bare "Memory" heading
            # would be a straightforwardly wrong claim — the first HTTP run of
            # this benchmark showed 4 MB growth for 12 uploads, which is only
            # true because the frames were never in this process.
            "measures": (
                "the LANA server process (same process as this benchmark)"
                if driver.url is None
                else "THIS BENCHMARK CLIENT, not the server — meaningless as a "
                     "server memory figure; run in-process to measure that"
            ),
            "server_process": driver.url is None,
            "rss_start_mb": round(rss_start / 1024 ** 2, 1),
            "rss_after_uploads_mb": round(rss_after_uploads / 1024 ** 2, 1),
            "rss_end_mb": round(rss_end / 1024 ** 2, 1),
            "growth_mb": round((rss_end - rss_start) / 1024 ** 2, 1),
            "sessions_created": len(session_ids),
        },
    }


def probe_refusal(driver: Driver) -> dict[str, Any]:
    """Ask for more than the budget allows, and check it is refused cleanly.

    The point is not that a huge upload fails — it is that it fails with a
    status code and a message, rather than by the process dying. A 413 or 503
    here is admission control working; a connection reset is an OOM.

    The budget is derived from host RAM, so on a large machine it can be
    hundreds of megabytes and generating a genuinely oversized payload would
    cost more than the benchmark. In-process, the ceiling is lowered for the
    duration of the probe instead, which exercises the identical code path
    against a payload that costs nothing to build. Over HTTP the server's
    limits cannot be reached from here, so the probe reports honestly that it
    could not force a refusal rather than implying it tested one.
    """
    status, response = driver.request("GET", "/health")
    limits = response.json()["limits"] if status == 200 else {}
    real_limit_mb = limits.get("max_upload_mb", 0)

    payload = b"a,b,c\n" + (b"1,2,3\n" * 200_000)   # ~1.2 MB
    probe_limit_mb = 1
    forced = False

    if driver.url is None:
        import backend.main as backend_main

        original = backend_main.MAX_UPLOAD_MB
        backend_main.MAX_UPLOAD_MB = probe_limit_mb
        forced = True
        try:
            over_status, over_response = driver.request(
                "POST", "/upload",
                files={"file": ("huge.csv", payload, "text/csv")},
            )
        finally:
            backend_main.MAX_UPLOAD_MB = original
    else:
        over_status, over_response = driver.request(
            "POST", "/upload",
            files={"file": ("huge.csv", payload, "text/csv")},
        )

    detail = ""
    if over_status >= 400:
        try:
            detail = str(over_response.json().get("detail", ""))[:200]
        except Exception:
            detail = over_response.text[:200]
    else:
        detail = (
            "accepted — payload is within this host's budget; run with --url "
            "against a server configured with a low LANA_MAX_UPLOAD_MB to "
            "force the refusal path over real HTTP"
        )

    return {
        "host_limit_mb": real_limit_mb,
        "probe_limit_mb": probe_limit_mb if forced else real_limit_mb,
        "limit_forced_for_probe": forced,
        "offered_bytes": len(payload),
        "status": over_status,
        # A refusal is a 413 (too large) or 503 (not enough free memory now).
        # A 200 means the payload fit, which proves nothing either way.
        "refused": over_status in (413, 503),
        "refused_cleanly": over_status in (200, 400, 413, 503),
        "detail": detail,
        "process_alive": driver.request("GET", "/health")[0] == 200,
    }


def render(report: dict[str, Any], refusal: dict[str, Any]) -> str:
    lines = [
        "=" * 78,
        f"LANA load test — {report['mode']}",
        f"concurrency={report['concurrency']}  dataset={report['dataset_rows']:,} rows",
        "=" * 78,
        "",
        f"{'endpoint':<24}{'n':>6}{'ok':>6}{'err':>5}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}",
        "-" * 78,
    ]
    rows = [report["uploads"], *report["endpoints"]]
    for entry in rows:
        lines.append(
            f"{entry['name']:<24}{entry['n']:>6}{entry['ok']:>6}{entry['errors']:>5}"
            f"{entry['p50_ms']:>8.1f}m{entry['p95_ms']:>8.1f}m"
            f"{entry['p99_ms']:>8.1f}m{entry['max_ms']:>8.1f}m"
        )

    totals, memory = report["totals"], report["memory"]
    lines += [
        "-" * 78,
        f"{'TOTAL':<24}{totals['requests']:>6}{'':>6}{totals['errors']:>5}"
        f"   {totals['throughput_rps']:>8.1f} req/s over {totals['wall_seconds']}s",
        "",
        "Memory — " + memory["measures"],
        f"  RSS at start          {memory['rss_start_mb']:>8.1f} MB",
        f"  RSS after uploads     {memory['rss_after_uploads_mb']:>8.1f} MB "
        f"({memory['sessions_created']} sessions)",
        f"  RSS at end            {memory['rss_end_mb']:>8.1f} MB",
        f"  Growth                {memory['growth_mb']:>8.1f} MB",
        "",
        "Admission control under an oversized upload",
        f"  host upload limit     {refusal['host_limit_mb']} MB",
        f"  limit used for probe  {refusal['probe_limit_mb']} MB"
        + ("  (lowered for the probe)" if refusal["limit_forced_for_probe"] else ""),
        f"  offered               {refusal['offered_bytes'] / 1024 ** 2:.1f} MB",
        f"  status                {refusal['status']}",
        f"  refused               {refusal['refused']}",
        f"  process still serving {refusal['process_alive']}",
        f"  detail                {refusal['detail']}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument(
        "--requests", type=int, default=30,
        help="requests per endpoint (total is this times the endpoint count)",
    )
    parser.add_argument("--rows", type=int, default=20_000)
    parser.add_argument(
        "--url", default=None,
        help="benchmark a running server over HTTP instead of the ASGI app in-process",
    )
    parser.add_argument("--json", default=None, help="write the full report as JSON")
    args = parser.parse_args()

    # The limiter would otherwise refuse most of a deliberate burst, which is
    # correct behaviour and completely defeats the measurement.
    os.environ.setdefault("LANA_RATE_CAPACITY", "1000000")
    os.environ.setdefault("LANA_RATE_REFILL_PER_SECOND", "1000000")
    os.environ.setdefault("LANA_LLM_RATE_CAPACITY", "1000000")

    driver = Driver(args.url)
    try:
        report = run_load(
            driver,
            concurrency=args.concurrency,
            requests_per_endpoint=args.requests,
            rows=args.rows,
        )
        refusal = probe_refusal(driver)
    finally:
        driver.close()

    print(render(report, refusal))

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"load": report, "admission": refusal}, indent=2),
            encoding="utf-8",
        )
        print(f"Wrote {path}")

    # Non-zero on a 5xx: this is runnable in CI as a smoke gate, where a
    # crash under concurrency should fail the build rather than print nicely.
    return 1 if report["totals"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
