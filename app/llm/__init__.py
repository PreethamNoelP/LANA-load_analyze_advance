import threading

from ..config import config
from .base import LLMProvider
from .ollama_provider import OllamaProvider
from .openai_compat import OpenAICompatProvider

# Providers are stateless apart from their HTTP client, and rebuilding that
# client per request throws away connection reuse — noticeable when a local
# model is answering several questions in a row. Configuration is read once
# at import, so a single instance stays correct for the process' lifetime.
_provider: LLMProvider | None = None
_lock = threading.Lock()


def _build_provider() -> LLMProvider:
    if config.llm.provider == "ollama":
        return OllamaProvider(
            model=config.llm.model,
            host=config.llm.ollama_host,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            timeout=config.llm.timeout,
            num_ctx=config.llm.num_ctx,
        )
    if config.llm.provider == "openai_compat":
        return OpenAICompatProvider(
            model=config.llm.model,
            base_url=config.llm.openai_compat_base_url,
            api_key=config.llm.openai_compat_api_key,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            timeout=config.llm.timeout,
        )
    raise ValueError(
        f"Unknown LLM provider '{config.llm.provider}'. "
        "Set LLM_PROVIDER to 'ollama' or 'openai_compat' in your .env file."
    )


def get_provider() -> LLMProvider:
    """Return the process-wide provider, constructing it on first use."""
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                _provider = _build_provider()
    return _provider
