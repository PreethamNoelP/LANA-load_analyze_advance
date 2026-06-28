from abc import ABC, abstractmethod
from typing import Optional


class LLMProvider(ABC):
    """Abstract base class for all LLM backends."""

    @abstractmethod
    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Send a prompt and return the model's text response."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check whether the backend is reachable and ready."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name shown in the UI."""

    def answer_question(self, question: str, data_context: str) -> str:
        system = (
            "You are a data analyst assistant. "
            "Answer the user's question using only the dataset context provided. "
            "Be concise, factual, and highlight key numbers when relevant."
        )
        prompt = f"Dataset context:\n{data_context}\n\nQuestion: {question}"
        return self.generate(prompt, system_prompt=system)