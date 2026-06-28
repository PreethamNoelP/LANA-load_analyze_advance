from typing import Optional
from .base import LLMProvider


class OllamaProvider(LLMProvider):
    """Local LLM via Ollama (https://ollama.ai).

    Supports any model available through `ollama pull`, e.g.:
      llama3.1:8b, mistral:7b, phi3:mini, gemma2:2b, qwen2.5:7b
    """

    def __init__(self, model: str = "llama3.1:8b", host: str = "http://localhost:11434"):
        self.model = model
        self.host = host
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import ollama
                self._client = ollama.Client(host=self.host)
            except ImportError:
                raise ImportError(
                    "The 'ollama' package is required. Install it with: pip install ollama"
                )
        return self._client

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        client = self._get_client()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = client.chat(model=self.model, messages=messages)
        return response["message"]["content"]

    def is_available(self) -> bool:
        try:
            self._get_client().list()
            return True
        except Exception:
            return False

    def list_local_models(self) -> list[str]:
        """Return names of models already pulled locally."""
        try:
            result = self._get_client().list()
            return [m["name"] for m in result.get("models", [])]
        except Exception:
            return []

    @property
    def name(self) -> str:
        return f"Ollama — {self.model}"