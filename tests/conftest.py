import os
import sys
from pathlib import Path

# Make `backend` and `app` importable regardless of where pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Rate limits are read from the environment when `app.config` is imported, and
# `app.config` is imported the first time any test touches `backend.main`.
# conftest runs before that, so setting them here is what makes the suite
# deterministic.
#
# This is not the limiter being switched off — `tests/test_rate_limiting.py`
# exercises it directly with its own low limits, which is the honest way to
# test a limiter. It is the suite declining to share one 8-token bucket across
# 300+ tests, where a passing run would otherwise depend on how fast the
# machine got through the earlier files.
os.environ.setdefault("LANA_RATE_CAPACITY", "100000")
os.environ.setdefault("LANA_RATE_REFILL_PER_SECOND", "100000")
os.environ.setdefault("LANA_LLM_RATE_CAPACITY", "100000")
os.environ.setdefault("LANA_LLM_RATE_REFILL_PER_SECOND", "100000")

# Coordination goes through the in-process backend during tests. The SQLite
# one is exercised on its own in tests/test_coordination.py; using it for
# everything would put a shared file in the loop of every API test for no
# added coverage.
os.environ.setdefault("LANA_COORDINATION", "memory")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_llm_probe_cache():
    """Drop /health's cached LLM reachability between tests.

    /health caches the provider probe for a few seconds, because when Ollama
    is down each probe costs a full connection timeout (measured at p95 7.9s
    under load before the cache existed). That cache is correct in production
    and poison in a test suite, where the provider is monkeypatched per test
    and the next test would otherwise read the previous test's answer.
    """
    import backend.main as backend_main

    backend_main.reset_llm_probe_cache()
    yield
    backend_main.reset_llm_probe_cache()
