import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

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
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    allowed_origins: list[str] = field(default_factory=_parse_allowed_origins)


config = AppConfig()