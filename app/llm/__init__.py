from .base import LLMProvider
from .ollama_provider import OllamaProvider
from .openai_compat import OpenAICompatProvider
from ..config import config


def get_provider() -> LLMProvider:
    if config.llm.provider == "ollama":
        return OllamaProvider(
            model=config.llm.model,
            host=config.llm.ollama_host,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
    elif config.llm.provider == "openai_compat":
        return OpenAICompatProvider(
            model=config.llm.model,
            base_url=config.llm.openai_compat_base_url,
            api_key=config.llm.openai_compat_api_key,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
    else:
        raise ValueError(
            f"Unknown LLM provider '{config.llm.provider}'. "
            "Set LLM_PROVIDER to 'ollama' or 'openai_compat' in your .env file."
        )