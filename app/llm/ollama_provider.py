from .base import LLMProvider


class OllamaProvider(LLMProvider):
    """Local LLM via Ollama (https://ollama.ai).

    Supports any model pulled locally, e.g.:
      ollama pull phi3:mini | llama3.1:8b | mistral:7b | gemma2:2b
    """

    def __init__(
        self,
        model: str = "llama3.1:8b",
        host: str = "http://localhost:11434",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ):
        self.model = model
        self.host = host
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import ollama
                self._client = ollama.Client(host=self.host)
            except ImportError:
                raise ImportError("Install the ollama package: pip install ollama")
        return self._client

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = self._get_client().chat(
            model=self.model,
            messages=messages,
            options={"temperature": self.temperature, "num_predict": self.max_tokens},
        )
        return response.message.content

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
            return [m.model for m in result.models]
        except Exception:
            return []

    @property
    def name(self) -> str:
        return f"Ollama — {self.model}"