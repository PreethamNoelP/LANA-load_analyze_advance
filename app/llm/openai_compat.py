from typing import Optional
from .base import LLMProvider


class OpenAICompatProvider(LLMProvider):
    """Any OpenAI-compatible API endpoint.

    Works out-of-the-box with:
      - Groq          (https://api.groq.com/openai/v1)
      - Together.ai   (https://api.together.xyz/v1)
      - LM Studio     (http://localhost:1234/v1)
      - Fireworks.ai  (https://api.fireworks.ai/inference/v1)
      - Anyscale      (https://api.endpoints.anyscale.com/v1)

    Set OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_API_KEY, and LLM_MODEL in .env.
    """

    def __init__(self, model: str, base_url: str, api_key: str = "dummy"):
        self.model = model
        self.base_url = base_url
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(base_url=self.base_url, api_key=self._api_key)
            except ImportError:
                raise ImportError(
                    "The 'openai' package is required. Install it with: pip install openai"
                )
        return self._client

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        client = self._get_client()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(model=self.model, messages=messages)
        return response.choices[0].message.content

    def is_available(self) -> bool:
        try:
            self._get_client().models.list()
            return True
        except Exception:
            return False

    @property
    def name(self) -> str:
        return f"OpenAI-compat — {self.model}"