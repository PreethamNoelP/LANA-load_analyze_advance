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
    # drop columns instead of erroring. Ignored by the openai_compat provider,
    # whose hosted context windows are already generous.
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


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    allowed_origins: list[str] = field(default_factory=_parse_allowed_origins)
    limits: LimitsConfig = field(default_factory=LimitsConfig)


config = AppConfig()