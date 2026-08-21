from abc import ABC, abstractmethod
from collections.abc import Iterator

# The system prompt is the first line of defence against a confident wrong
# answer. Its job is to make "the data does not say" a cheaper response for
# the model than inventing a figure — small local models default to filling
# gaps fluently unless refusal is explicitly licensed and demonstrated.
#
# Rule 2 originally licensed refusal with one concrete, worked example.
# eval/ (run against phi3:mini) showed the model pattern-matching that
# example almost verbatim and reusing it as a refusal template regardless of
# whether the fact was actually present — including on trivial questions the
# DATASET FACTS section answers directly. Small models overfitting to a
# single few-shot example is a known failure mode; the fix is a "check
# before you refuse" instruction plus a contrasting example of the
# present-fact case, so the model has two patterns to discriminate between
# instead of one to copy.
ANSWER_SYSTEM_PROMPT = """You are LANA's data analyst. You answer questions strictly from the dataset facts supplied to you.

RULES — follow all of them:

1. GROUNDING. Use only numbers that appear in the DATASET FACTS section, or that you compute from them. Show which stated figures a computed number came from. Never estimate, guess, or recall a number from general knowledge.

2. CHECK BEFORE YOU REFUSE. A refusal is only correct when the fact is genuinely absent — re-read the DATASET FACTS and COLUMNS sections above before deciding something is missing.
   - If the fact IS stated above: answer directly and name the source, e.g. "The mean revenue is 221.9, from the 'revenue' column above."
   - If the fact is NOT stated above: say exactly what is missing, e.g. "The facts above do not include per-customer revenue; they list revenue totals by region only." Describe what THIS question is actually missing, in your own words — do not reuse that sentence itself as a template for every question.
   Never substitute a plausible-sounding figure for a fact that is truly absent. But claiming a fact is missing when it is stated above is just as wrong as inventing one — both are answers not grounded in what was actually given.

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
        #
        # eval/ (run against phi3:mini) traced most of that model's
        # over-refusal to this trailing instruction itself: ending on "if
        # they do not contain what is needed, say so" put the refusal branch
        # in the single most-attended position in the entire prompt, so the
        # model defaulted to it even when the fact was stated plainly earlier
        # in the same context. Ending on a search-and-answer instruction
        # instead — with refusal framed explicitly as the last resort rather
        # than modelled as the sentence to produce — fixed retrieval on every
        # case tested without weakening genuine refusals; see eval/results/.
        return (
            f"{data_context}\n\n"
            "=== QUESTION ===\n"
            f"{question}\n\n"
            "Search COLUMNS, CATEGORY BREAKDOWNS, and GROUP AVERAGES above for "
            "the exact fact this question needs, and state it directly when "
            "you find it. Treat \"not available\" as a last resort, not a "
            "default."
        )

    def answer_question(self, question: str, data_context: str) -> str:
        return self.generate(self._build_prompt(question, data_context),
                             system_prompt=ANSWER_SYSTEM_PROMPT)

    def answer_question_stream(self, question: str, data_context: str) -> Iterator[str]:
        yield from self.generate_stream(self._build_prompt(question, data_context),
                                        system_prompt=ANSWER_SYSTEM_PROMPT)
