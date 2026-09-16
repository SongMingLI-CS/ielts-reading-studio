"""Deterministic quality gates for passages and assessments."""

from .passage import validate_passage
from .questions import validate_questions

__all__ = ["validate_passage", "validate_questions"]
