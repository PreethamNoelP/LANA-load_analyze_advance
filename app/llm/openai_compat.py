from collections.abc import Iterator

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
        timeout: float = 90.0,
    ):
        self.model = model
        self.base_url = base_url
        self._api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                # Local endpoints (LM Studio etc.) need no key, but the SDK
                # refuses an empty one — send a placeholder instead.
                self._client = OpenAI(
                    base_url=self.base_url,
                    api_key=self._api_key or "not-needed",
                    timeout=self.timeout,
                )
            except ImportError as e:
                raise ImportError("Install the openai package: pip install openai") from e
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
        # Same guard the streaming path has had all along: `choices` comes back
        # empty when a provider filters the response, and indexing it blindly
        # turns that into "list index out of range" — an error that tells the
        # user nothing about what actually happened.
        if not response.choices:
            raise RuntimeError(
                "The model returned no choices. The provider may have filtered "
                "the response, or the model name may be wrong."
            )
        # The API types content as optional, and a filtered or empty completion
        # really can return None. The ABC promises a str.
        return response.choices[0].message.content or ""

    def generate_stream(self, prompt: str, system_prompt: str | None = None) -> Iterator[str]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        stream = self._get_client().chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        for chunk in stream:
            # `choices` is empty on the usage-only final chunk that Azure and
            # several OpenAI-compatible proxies emit. Indexing it blindly
            # raises IndexError mid-answer, which surfaces as the stream dying
            # partway through a reply that was otherwise fine.
            if not chunk.choices:
                continue
            content = chunk.choices[0].delta.content
            if content:
                yield content

    def is_available(self) -> bool:
        try:
            self._get_client().models.list()
            return True
        except Exception:
            return False

    @property
    def name(self) -> str:
        return f"OpenAI-compat — {self.model}"