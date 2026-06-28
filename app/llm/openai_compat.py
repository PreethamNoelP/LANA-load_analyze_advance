from .base import LLMProvider


class OpenAICompatProvider(LLMProvider):
    """Any OpenAI-compatible REST endpoint.

    Tested with:
      - Groq          (https://api.groq.com/openai/v1)
      - Together.ai   (https://api.together.xyz/v1)
      - LM Studio     (http://localhost:1234/v1)
      - Fireworks.ai  (https://api.fireworks.ai/inference/v1)

    Set OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_API_KEY, and LLM_MODEL in .env.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ):
        self.model = model
        self.base_url = base_url
        self._api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(base_url=self.base_url, api_key=self._api_key)
            except ImportError:
                raise ImportError("Install the openai package: pip install openai")
        return self._client

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = self._get_client().chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
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