"""LANA's evaluation harness.

Tests the specific, falsifiable claim the product makes: that grounding a
local LLM in a machine-computed fact set, plus checking its answer against
that fact set afterward, produces fewer unsupported numerical claims than
handing the model a dataset and a question with no such scaffolding.

Two independent measurements live here:

* ``eval/harness.py`` + ``eval/run.py`` — a baseline-vs-LANA comparison run
  through a real, running local model over a labeled question set
  (``eval/cases.py``) with ground truth resolved from the same production
  statistics code LANA itself uses (``eval/ground_truth.py``).
* ``eval/adversarial.py`` — a model-free suite of scripted right and wrong
  answers that measures the validator (``app.llm.validation``) in isolation,
  producing a precision/recall/F1 confusion matrix over the failure modes it
  is designed to catch, and a separate, honest catch-rate over the failure
  modes it is explicitly not designed to catch.

Nothing in this package is mocked when it runs against a live model — see
``eval/run.py``.
"""
