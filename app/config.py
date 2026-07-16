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


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)


config = AppConfig()