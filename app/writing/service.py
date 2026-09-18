from __future__ import annotations

import json
import math
from uuid import uuid4

from pydantic import ValidationError

from app.agents.base import AgentSchemaError, JsonProvider, ModelRequest
from app.config import AppConfig, redact_secrets
from app.storage.repositories import Repository

from .models import (
    ModelWritingEvaluation,
    WritingEvaluationRecord,
    WritingEvaluationRequest,
    WritingEvaluationResponse,
    WritingTaskType,
)

WRITING_EVALUATION_PROMPT_VERSION = "writing-evaluation-v1"
WRITING_EVALUATION_SYSTEM = """You are a rigorous IELTS writing examiner.
Evaluate the supplied response using the official four criteria. For Task 1,
task_fulfilment means Task Achievement; for Task 2 it means Task Response.
Use only bands from 0 to 9 in 0.5 increments. Base every comment on the submitted
essay and do not invent quotations. Return one JSON object only, with exactly:
task_fulfilment, coherence_and_cohesion, lexical_resource, and
grammatical_range_and_accuracy as objects containing band and feedback;
general_feedback as a string; strengths and improvements as non-empty arrays of
strings; and sample_answer as a string or null. If include_sample_answer is false,
sample_answer must be null. Do not include an overall band; it is calculated by
the application."""


class WritingEvaluationService:
    def __init__(
        self,
        provider: JsonProvider,
        config: AppConfig,
        repository: Repository,
    ) -> None:
        self.provider = provider
        self.config = config
        self.repository = repository

    def evaluate(self, submission: WritingEvaluationRequest) -> WritingEvaluationResponse:
        result = self.provider.complete_json(
            ModelRequest(
                stage="writing_evaluation",
                model=self.config.writing_model,
                system=WRITING_EVALUATION_SYSTEM,
                user=_user_prompt(submission),
                max_tokens=self.config.writing_max_output_tokens,
                temperature=0.1,
            )
        )
        try:
            evaluation = ModelWritingEvaluation.model_validate(result.payload)
        except ValidationError as exc:
            raise AgentSchemaError(
                f"writing_evaluation schema validation failed: {redact_secrets(exc)}"
            ) from None

        criteria = [
            evaluation.task_fulfilment,
            evaluation.coherence_and_cohesion,
            evaluation.lexical_resource,
            evaluation.grammatical_range_and_accuracy,
        ]
        overall_band = _round_ielts_band(sum(item.band for item in criteria) / 4)
        identifier = str(uuid4())
        task_criterion = (
            {"task_achievement": evaluation.task_fulfilment}
            if submission.task_type == WritingTaskType.TASK_1
            else {"task_response": evaluation.task_fulfilment}
        )
        response = WritingEvaluationResponse(
            id=identifier,
            task_type=submission.task_type,
            overall_band=overall_band,
            coherence_and_cohesion=evaluation.coherence_and_cohesion,
            lexical_resource=evaluation.lexical_resource,
            grammatical_range_and_accuracy=evaluation.grammatical_range_and_accuracy,
            general_feedback=evaluation.general_feedback,
            strengths=evaluation.strengths,
            improvements=evaluation.improvements,
            sample_answer=evaluation.sample_answer if submission.include_sample_answer else None,
            **task_criterion,
        )
        self.repository.add_writing_evaluation(
            WritingEvaluationRecord(
                request=submission,
                response=response,
                model=result.model or self.config.writing_model,
                prompt_version=WRITING_EVALUATION_PROMPT_VERSION,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                elapsed_ms=result.elapsed_ms,
                retries=result.retries,
            )
        )
        return response


def _user_prompt(submission: WritingEvaluationRequest) -> str:
    return "Evaluate this IELTS writing submission. JSON input:\n" + json.dumps(
        submission.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )


def _round_ielts_band(value: float) -> float:
    """Round a criterion average to the nearest whole or half band."""
    return min(9.0, max(0.0, math.floor(value * 2 + 0.5) / 2))