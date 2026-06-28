from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Abstract base for LLM backends. Implement generate() and is_available()."""

    @abstractmethod
    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        """Send a prompt and return the model's text response."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the backend is reachable and ready."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name shown in the UI (e.g. 'Ollama — phi3:mini')."""

    def answer_question(self, question: str, data_context: str) -> str:
        system = (
            "You are a data analyst assistant. "
            "Answer the user's question using only the dataset context provided. "
            "Be concise, factual, and highlight key numbers when relevant."
        )
        prompt = f"Dataset context:\n{data_context}\n\nQuestion: {question}"
        return self.generate(prompt, system_prompt=system)