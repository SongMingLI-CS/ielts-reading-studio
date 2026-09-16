from __future__ import annotations

from copy import deepcopy

import pytest

from app.agents.base import ModelRequest, ModelResult
from app.config import AppConfig
from app.models import Difficulty, GenerationUnit, QuestionType, UnitStatus


class RecordingProvider:
    def __init__(self):
        self.payloads: list[dict] = []
        self.requests: list[ModelRequest] = []

    def queue(self, payload: dict) -> None:
        self.payloads.append(deepcopy(payload))

    def complete_json(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        payload = self.payloads.pop(0)
        return ModelResult(
            payload=payload,
            raw_text="{}",
            input_tokens=10,
            output_tokens=5,
            model=request.model,
            finish_reason="stop",
        )


@pytest.fixture
def recording_provider():
    return RecordingProvider()


@pytest.fixture
def agent_config(tmp_path):
    return AppConfig(
        base_dir=tmp_path,
        author_model="author-model",
        examiner_model="examiner-model",
    )


@pytest.fixture
def unit():
    return GenerationUnit(
        id="u1",
        corpus_id="corpus-1",
        source_chapter_ids=["chapter-1"],
        source_text_hash="hash",
        difficulty=Difficulty.STANDARD,
        question_types=[
            QuestionType.MATCHING_HEADINGS,
            QuestionType.TRUE_FALSE_NOT_GIVEN,
            QuestionType.SUMMARY_COMPLETION,
        ],
        config_snapshot={},
        prompt_version="1",
        status=UnitStatus.INDEXED,
    )
