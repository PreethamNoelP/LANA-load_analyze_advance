from abc import ABC, abstractmethod
from collections.abc import Iterator

# The system prompt is the first line of defence against a confident wrong
# answer. Its job is to make "the data does not say" a cheaper response for
# the model than inventing a figure — small local models default to filling
# gaps fluently unless refusal is explicitly licensed and demonstrated.
ANSWER_SYSTEM_PROMPT = """You are LANA's data analyst. You answer questions strictly from the dataset facts supplied to you.

RULES — follow all of them:

1. GROUNDING. Use only numbers that appear in the DATASET FACTS section, or that you compute from them. Show which stated figures a computed number came from. Never estimate, guess, or recall a number from general knowledge.

2. REFUSAL IS A CORRECT ANSWER. If the facts do not contain what was asked, say exactly what is missing and stop. For example: "The provided data does not include per-customer revenue, so I cannot answer that. It has revenue totals by region only." Never substitute a plausible-sounding figure. An honest "not available" is more useful than a fluent guess.

3. NO CAUSAL LANGUAGE. Correlations and regression fits are associations. Write "is associated with" or "moves together with", never "causes", "drives", "leads to", or "because of".

4. RESPECT THE CAVEATS. Where a column is flagged as skewed, use the median rather than the mean and say why. Where values were imputed or rows removed, state that the figure rests on transformed data. Where a relationship failed false-discovery correction, report it as unproven rather than as a finding.

5. UNCERTAINTY TRAVELS WITH THE NUMBER. If a confidence interval or sample size is given for a figure you quote, quote it too. A mean from 12 rows must not be presented like a mean from 12,000.

6. BE CONCISE AND SPECIFIC. Lead with the direct answer, then the evidence. Quote exact column names. No preamble, no restating the question, no filler."""


class LLMProvider(ABC):
    """Abstract base for LLM backends. Implement generate() and is_available()."""

    @abstractmethod
    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        """Send a prompt and return the model's text response."""

    @abstractmethod
    def generate_stream(self, prompt: str, system_prompt: str | None = None) -> Iterator[str]:
        """Send a prompt and yield the model's response incrementally, chunk by chunk."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the backend is reachable and ready."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name shown in the UI (e.g. 'Ollama — phi3:mini')."""

    def _build_prompt(self, question: str, data_context: str) -> str:
        # The question is placed last: models attend most reliably to the end
        # of a long prompt, and the context block dominates the token count.
        return (
            f"{data_context}\n\n"
            "=== QUESTION ===\n"
            f"{question}\n\n"
            "Answer using only the facts above. If they do not contain what is "
            "needed, say so explicitly and name what is missing."
        )

    def answer_question(self, question: str, data_context: str) -> str:
        return self.generate(self._build_prompt(question, data_context),
                             system_prompt=ANSWER_SYSTEM_PROMPT)

    def answer_question_stream(self, question: str, data_context: str) -> Iterator[str]:
        yield from self.generate_stream(self._build_prompt(question, data_context),
                                        system_prompt=ANSWER_SYSTEM_PROMPT)
