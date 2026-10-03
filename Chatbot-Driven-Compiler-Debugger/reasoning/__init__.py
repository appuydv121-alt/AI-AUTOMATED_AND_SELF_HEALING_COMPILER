"""
Evidence-grounded C++ reasoning pipeline.

Public API
----------
    run_reasoning(source, run_result, config=None) -> ReasoningResult

The pipeline is additive: when any optional dependency (Ollama, tree-sitter)
is unavailable the result degrades to compiler-warning facts only.
"""

from reasoning.pipeline import run_reasoning          # noqa: F401
from reasoning.models import ReasoningResult, Finding  # noqa: F401

__all__ = ["run_reasoning", "ReasoningResult", "Finding"]
