import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from .resources import HOST

load_dotenv()


@dataclass
class LLMConfig:
    provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "ollama"))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "llama3.1:8b"))
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    openai_compat_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_COMPAT_BASE_URL", ""))
    openai_compat_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_COMPAT_API_KEY", ""))
    temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.3")))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "2048")))
    timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "90")))
    # Ollama's own default (2048-4096 tokens depending on model) is too small for
    # a wide dataset's context block — raise it so the model doesn't silently
    # drop columns instead of erroring.
    #
    # It is only passed *to the runtime* by the Ollama provider, but it is not
    # openai_compat-agnostic: build_context() uses it as the token budget for
    # every provider, so leaving it at 8192 while pointing LANA at a hosted
    # model with a 128k window makes LANA trim its own facts for no reason.
    # Raise it to match whatever window the configured model actually has.
    num_ctx: int = field(default_factory=lambda: int(os.getenv("LLM_NUM_CTX", "8192")))


_DEFAULT_ALLOWED_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]


def _parse_allowed_origins() -> list[str]:
    raw = os.getenv("LANA_ALLOWED_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if "*" in origins:
        raise ValueError(
            "LANA_ALLOWED_ORIGINS cannot include '*' — the API is served with "
            "allow_credentials=True, and browsers reject a wildcard origin "
            "combined with credentials. List explicit origins instead, e.g. "
            "LANA_ALLOWED_ORIGINS=https://your-app.example.com"
        )
    return origins or list(_DEFAULT_ALLOWED_ORIGINS)


@dataclass
class LimitsConfig:
    """Session/upload/concurrency limits.

    Upload and session-memory limits default to a value derived from this
    machine's actual RAM (``app.resources.HOST``) rather than a flat
    constant — the same build should not hand an 8 GB laptop and a 64 GB
    workstation the same 2 GB budget. An explicit env var always overrides
    the probe; the operator knows something it does not.
    """

    max_upload_mb: int = field(default_factory=lambda: int(os.getenv(
        "LANA_MAX_UPLOAD_MB", str(max(8, int(HOST.upload_limit_bytes() / 1024 ** 2)))
    )))
    # Resident-bytes ceiling across all sessions. Session count alone is not a
    # memory bound — a handful of wide uploads can exhaust the host well
    # before the count limit is reached.
    max_session_mb: int = field(default_factory=lambda: int(os.getenv(
        "LANA_MAX_SESSION_MB", str(max(256, int(HOST.session_budget_bytes() / 1024 ** 2)))
    )))
    max_sessions: int = field(default_factory=lambda: int(os.getenv("LANA_MAX_SESSIONS", "30")))
    session_ttl_seconds: float = field(
        default_factory=lambda: float(os.getenv("LANA_SESSION_TTL_SECONDS", "3600"))
    )
    # Caps how many LLM requests run at once — a local Ollama model serves one
    # request at a time well; without this, a burst of concurrent visitors all
    # queue behind it and every answer appears to hang.
    max_concurrent_llm: int = field(
        default_factory=lambda: int(os.getenv("LANA_MAX_CONCURRENT_LLM", "2"))
    )
    # Off by default: a plain `uvicorn --reload` dev run should not start
    # writing a data/ directory into a contributor's checkout unannounced.
    # The Docker image sets this explicitly — see docker-compose.yml — because
    # there a restart silently losing every session is the worse default.
    persist_sessions: bool = field(
        default_factory=lambda: os.getenv("LANA_PERSIST_SESSIONS", "false").lower()
        in ("1", "true", "yes")
    )
    data_dir: str = field(default_factory=lambda: os.getenv("LANA_DATA_DIR", "data"))

    # ── Rate limiting ────────────────────────────────────────────────────────
    # Token bucket per principal. `capacity` is the burst a client may spend
    # at once; `refill_per_second` is the sustained rate it recovers at.
    #
    # 120 burst / 2 per second suits interactive use — clicking through tabs
    # is maybe twenty requests a minute, and a page load that fires a dozen
    # calls at once must not trip it — while still bounding a runaway script.
    rate_capacity: int = field(
        default_factory=lambda: int(os.getenv("LANA_RATE_CAPACITY", "120"))
    )
    rate_refill_per_second: float = field(
        default_factory=lambda: float(os.getenv("LANA_RATE_REFILL_PER_SECOND", "2.0"))
    )
    # Questions are seconds of local inference, not milliseconds of pandas, so
    # they get their own far tighter bucket: a burst of twenty, then thirty a
    # minute sustained. Deliberately above what one person can actually
    # consume — a local model answering in 3-8s cannot be asked faster than
    # this anyway — so the limit bounds a runaway client without ever being
    # felt by a legitimate one. Concurrency is capped separately, by the LLM
    # slot leases, which is the control that protects the model itself.
    llm_rate_capacity: int = field(
        default_factory=lambda: int(os.getenv("LANA_LLM_RATE_CAPACITY", "20"))
    )
    llm_rate_refill_per_second: float = field(
        default_factory=lambda: float(
            os.getenv("LANA_LLM_RATE_REFILL_PER_SECOND", "0.5")
        )
    )

    # How long a browser's auth cookie stays valid. Twelve hours: long enough
    # that a working day needs one sign-in, short enough that a shared machine
    # does not stay authenticated indefinitely.
    auth_cookie_max_age: int = field(
        default_factory=lambda: int(os.getenv("LANA_AUTH_COOKIE_MAX_AGE", "43200"))
    )

    # Rows a connector may pull in one load. Remote sources have no natural
    # size limit, so this is the equivalent of the upload cap for everything
    # that is not a file upload.
    max_source_rows: int = field(
        default_factory=lambda: int(os.getenv("LANA_MAX_SOURCE_ROWS", "200000"))
    )

    # Whether a question may be answered by generating and executing SQL
    # against the session's data (app/analysis/sql_engine.py). On by default:
    # it is measurably more accurate than the fact ledger and is sandboxed.
    # Set false to fall back to ledger-only grounding everywhere.
    sql_grounding: bool = field(
        default_factory=lambda: os.getenv("LANA_SQL_GROUNDING", "true").lower()
        in ("1", "true", "yes")
    )


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    allowed_origins: list[str] = field(default_factory=_parse_allowed_origins)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    # Empty (the default) disables auth entirely — every endpoint is open, as
    # LANA has always assumed for a single local user. Set LANA_AUTH_TOKEN to
    # require `Authorization: Bearer <token>` on every request, e.g. when
    # running LANA on a machine reachable by more than just you.
    auth_token: str = field(default_factory=lambda: os.getenv("LANA_AUTH_TOKEN", ""))


config = AppConfig()