from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from app.agents.base import AgentSchemaError, ModelResult, ProviderError
from app.agents.examiner import AssessmentPayload, PassageReview
from app.config import AppConfig, redact_secrets
from app.models import (
    GenerationUnit,
    QualityReport,
    QuestionGroup,
    ReadingPackage,
    ReadingPassage,
    SourceBrief,
    UnitStatus,
    UsageRecord,
)
from app.planning.units import question_total
from app.storage.artifacts import ArtifactStore
from app.storage.cache import stage_cache_key
from app.storage.repositories import Repository
from app.validators.passage import validate_passage
from app.validators.questions import validate_questions

from .state import can_transition

T = TypeVar("T")


@dataclass
class UnitRunResult:
    unit_id: str
    status: UnitStatus
    package: ReadingPackage | None = None
    error_code: str | None = None
    author_revisions: int = 0
    examiner_revisions: int = 0


class UnitRunner:
    """Run one unit serially, persisting every model stage before continuing."""

    def __init__(
        self,
        *,
        config: AppConfig,
        repository: Repository,
        store: ArtifactStore,
        author: Any,
        examiner: Any,
        source_text_loader: Callable[[GenerationUnit], str],
        passage_validator: Callable[..., QualityReport] = validate_passage,
        question_validator: Callable[..., QualityReport] = validate_questions,
        after_stage: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.store = store
        self.author = author
        self.examiner = examiner
        self.source_text_loader = source_text_loader
        self.passage_validator = passage_validator
        self.question_validator = question_validator
        self.after_stage = after_stage

    def run(self, unit_id: str, *, job_id: str | None = None) -> UnitRunResult:
        try:
            return self._run(unit_id, job_id=job_id)
        except ProviderError as exc:
            unit = self._required_unit(unit_id)
            if can_transition(unit.status, UnitStatus.FAILED):
                self.repository.transition(unit.id, unit.status, UnitStatus.FAILED)
            self._write_failure(
                unit,
                exc.code,
                [
                    {
                        "code": exc.code,
                        "message": redact_secrets(exc),
                        "retries": exc.retries,
                    }
                ],
            )
            raise
        except AgentSchemaError as exc:
            unit = self._required_unit(unit_id)
            if can_transition(unit.status, UnitStatus.FAILED):
                self.repository.transition(unit.id, unit.status, UnitStatus.FAILED)
            self._write_failure(
                unit,
                "agent_schema_error",
                [{"code": "agent_schema_error", "message": redact_secrets(exc)}],
            )
            return UnitRunResult(
                unit_id=unit.id,
                status=UnitStatus.FAILED,
                error_code="agent_schema_error",
            )

    def _run(self, unit_id: str, *, job_id: str | None = None) -> UnitRunResult:
        unit = self._required_unit(unit_id)
        if unit.status in {UnitStatus.COMPLETED, UnitStatus.NEEDS_REVIEW, UnitStatus.CANCELLED}:
            return UnitRunResult(unit_id=unit.id, status=unit.status)
        self._move(unit.id, UnitStatus.AUTHOR_GENERATING)
        source_text = self.source_text_loader(unit)

        brief = self._stage(
            unit,
            job_id,
            "author_brief",
            model=self.config.author_model,
            owner=self.author,
            parameters={},
            invoke=lambda: self.author.create_brief(unit, source_text),
            encode=lambda value: value.model_dump(mode="json"),
            decode=SourceBrief.model_validate,
        )
        passage = self._stage(
            unit,
            job_id,
            "author_passage",
            model=self.config.author_model,
            owner=self.author,
            parameters={"brief": brief.model_dump(mode="json")},
            invoke=lambda: self.author.write_passage(unit, brief, source_text),
            encode=lambda value: value.model_dump(mode="json"),
            decode=ReadingPassage.model_validate,
        )
        self._move(unit.id, UnitStatus.PASSAGE_REVIEWING)

        author_revisions = 0
        passage_report = QualityReport(passed=False)
        while True:
            passage_report = self.passage_validator(source_text, brief, passage)
            self.store.write_json(
                f"reports/{unit.id}/passage-validation-{author_revisions}.json",
                passage_report,
            )
            review: PassageReview | None = None
            if passage_report.passed:
                review = self._stage(
                    unit,
                    job_id,
                    f"examiner_passage_review_{author_revisions}",
                    model=self.config.examiner_model,
                    owner=self.examiner,
                    parameters={"passage": passage.model_dump(mode="json")},
                    invoke=lambda passage=passage: self.examiner.review_passage(
                        unit, brief, source_text, passage
                    ),
                    encode=lambda value: value.model_dump(mode="json"),
                    decode=PassageReview.model_validate,
                )
            if passage_report.passed and review is not None and review.passed:
                break
            issues = _passage_issues(passage_report, review)
            if author_revisions >= self.config.author_revision_limit:
                self._move(unit.id, UnitStatus.NEEDS_REVIEW)
                self._write_failure(unit, "author_revision_limit", issues)
                return UnitRunResult(
                    unit_id=unit.id,
                    status=UnitStatus.NEEDS_REVIEW,
                    error_code="author_revision_limit",
                    author_revisions=author_revisions,
                )
            self._move(unit.id, UnitStatus.AUTHOR_REVISION_REQUIRED)
            self._move(unit.id, UnitStatus.AUTHOR_GENERATING)
            author_revisions += 1
            passage = self._stage(
                unit,
                job_id,
                f"author_passage_revision_{author_revisions}",
                model=self.config.author_model,
                owner=self.author,
                parameters={"passage": passage.model_dump(mode="json"), "issues": issues},
                invoke=lambda passage=passage, issues=issues: self.author.revise_passage(
                    unit, brief, source_text, passage, issues
                ),
                encode=lambda value: value.model_dump(mode="json"),
                decode=ReadingPassage.model_validate,
            )
            self._move(unit.id, UnitStatus.PASSAGE_REVIEWING)

        self._move(unit.id, UnitStatus.EXAMINER_GENERATING)
        groups = self._stage(
            unit,
            job_id,
            "examiner_assessment",
            model=self.config.examiner_model,
            owner=self.examiner,
            parameters={"passage": passage.model_dump(mode="json")},
            invoke=lambda: self.examiner.build_assessment(unit, passage),
            encode=_encode_groups,
            decode=_decode_groups,
        )
        self._move(unit.id, UnitStatus.VALIDATING)

        examiner_revisions = 0
        while True:
            question_report = self.question_validator(
                passage,
                groups,
                expected_total=question_total(unit.difficulty),
            )
            self.store.write_json(
                f"reports/{unit.id}/question-validation-{examiner_revisions}.json",
                question_report,
            )
            if question_report.passed:
                break
            if examiner_revisions >= self.config.examiner_revision_limit:
                self._move(unit.id, UnitStatus.NEEDS_REVIEW)
                self._write_failure(
                    unit,
                    "examiner_revision_limit",
                    [issue.model_dump(mode="json") for issue in question_report.issues],
                )
                return UnitRunResult(
                    unit_id=unit.id,
                    status=UnitStatus.NEEDS_REVIEW,
                    error_code="examiner_revision_limit",
                    author_revisions=author_revisions,
                    examiner_revisions=examiner_revisions,
                )
            failed_group_ids = _failed_group_ids(question_report, groups)
            self._move(unit.id, UnitStatus.EXAMINER_REVISION_REQUIRED)
            self._move(unit.id, UnitStatus.EXAMINER_GENERATING)
            examiner_revisions += 1
            issue_payloads = [issue.model_dump(mode="json") for issue in question_report.issues]
            groups = self._stage(
                unit,
                job_id,
                f"examiner_assessment_revision_{examiner_revisions}",
                model=self.config.examiner_model,
                owner=self.examiner,
                parameters={"failed_group_ids": failed_group_ids, "issues": issue_payloads},
                invoke=lambda groups=groups,
                failed_group_ids=failed_group_ids,
                issue_payloads=issue_payloads: self.examiner.repair_assessment(
                    unit,
                    passage,
                    groups,
                    failed_group_ids,
                    issue_payloads,
                ),
                encode=_encode_groups,
                decode=_decode_groups,
            )
            self._move(unit.id, UnitStatus.VALIDATING)

        final_report = QualityReport(
            passed=True,
            source_coverage=passage_report.source_coverage,
            passage_word_count=passage_report.passage_word_count,
            paragraph_count=passage_report.paragraph_count,
            metrics={**passage_report.metrics, **question_report.metrics},
            revision_count=author_revisions + examiner_revisions,
        )
        package = ReadingPackage(
            unit=unit.model_copy(update={"status": UnitStatus.COMPLETED}),
            source_brief=brief,
            passage=passage,
            question_groups=groups,
            quality_report=final_report,
            usage_records=self.repository.list_usage_records(unit.id),
        )
        self.store.write_package(unit.id, package)
        self._move(unit.id, UnitStatus.COMPLETED)
        return UnitRunResult(
            unit_id=unit.id,
            status=UnitStatus.COMPLETED,
            package=package,
            author_revisions=author_revisions,
            examiner_revisions=examiner_revisions,
        )

    def _stage(
        self,
        unit: GenerationUnit,
        job_id: str | None,
        stage: str,
        *,
        model: str,
        owner: Any,
        parameters: dict[str, object],
        invoke: Callable[[], T],
        encode: Callable[[T], dict[str, Any]],
        decode: Callable[[dict[str, Any]], T],
    ) -> T:
        cache_key = stage_cache_key(
            stage,
            unit.source_text_hash,
            unit.difficulty.value,
            [value.value for value in unit.question_types],
            model,
            getattr(owner, "prompt_version", unit.prompt_version),
            parameters,
        )
        cached = self.repository.get_cached_stage(cache_key)
        if cached is not None:
            return decode(cached.payload)
        attempt = self.repository.next_stage_attempt_number(unit.id, stage)
        self.repository.create_stage_attempt(
            unit.id,
            stage,
            attempt,
            job_id=job_id,
            cache_key=cache_key,
            payload={"parameters": parameters},
        )
        previous_result = getattr(owner, "last_result", None)
        try:
            value = invoke()
            payload = encode(value)
            result = getattr(owner, "last_result", None)
            if isinstance(result, ModelResult) and result is not previous_result:
                self._record_model_result(unit.id, job_id, stage, attempt, result)
            artifact = self.store.write_stage_payload(unit.id, stage, attempt, payload)
            committed = self.repository.complete_stage_attempt(
                unit.id,
                stage,
                attempt,
                artifact_path=str(artifact),
                payload=payload,
            )
            if not committed:
                cached = self.repository.get_cached_stage(cache_key)
                if cached is None:
                    raise RuntimeError(f"Could not commit stage {stage}")
                if not self.repository.reuse_stage_attempt(
                    unit.id,
                    stage,
                    attempt,
                    artifact_path=cached.artifact_path,
                    payload=cached.payload,
                ):
                    raise RuntimeError(f"Could not record cache reuse for stage {stage}")
                value = decode(cached.payload)
            if self.after_stage is not None:
                self.after_stage(stage)
            return value
        except Exception as exc:
            self.repository.fail_stage_attempt(
                unit.id,
                stage,
                attempt,
                error=redact_secrets(exc),
            )
            if isinstance(exc, ProviderError):
                self.repository.add_usage_record(
                    unit.id,
                    UsageRecord(
                        stage=stage,
                        retries=exc.retries,
                        error_type=exc.code,
                    ),
                    job_id=job_id,
                )
            raise

    def _record_model_result(
        self,
        unit_id: str,
        job_id: str | None,
        stage: str,
        attempt: int,
        result: ModelResult,
    ) -> None:
        self.store.write_raw_response(
            unit_id,
            stage,
            attempt,
            {
                "response_id": result.response_id,
                "model": result.model,
                "finish_reason": result.finish_reason,
                "raw_text": result.raw_text,
                "payload": result.payload,
                "usage": {
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "elapsed_ms": result.elapsed_ms,
                    "retries": result.retries,
                },
            },
        )
        self.repository.add_usage_record(
            unit_id,
            UsageRecord(
                stage=stage,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                elapsed_ms=result.elapsed_ms,
                retries=result.retries,
            ),
            job_id=job_id,
        )

    def _move(self, unit_id: str, target: UnitStatus) -> None:
        unit = self._required_unit(unit_id)
        if unit.status == target:
            return
        if not can_transition(unit.status, target):
            raise RuntimeError(f"Invalid unit transition: {unit.status.value} -> {target.value}")
        if not self.repository.transition(unit_id, unit.status, target):
            raise RuntimeError(f"Concurrent unit transition prevented: {unit_id}")

    def _required_unit(self, unit_id: str) -> GenerationUnit:
        unit = self.repository.get_unit(unit_id)
        if unit is None:
            raise KeyError(f"Unknown generation unit: {unit_id}")
        return unit

    def _write_failure(self, unit: GenerationUnit, code: str, issues: list[dict[str, Any]]) -> None:
        self.store.write_json(
            f"failed/{unit.id}.json",
            {"unit_id": unit.id, "code": code, "issues": issues},
        )


def _encode_groups(groups: list[QuestionGroup]) -> dict[str, Any]:
    return AssessmentPayload(question_groups=groups).model_dump(mode="json")


def _decode_groups(payload: dict[str, Any]) -> list[QuestionGroup]:
    return AssessmentPayload.model_validate(payload).question_groups


def _passage_issues(
    report: QualityReport,
    review: PassageReview | None,
) -> list[dict[str, Any]]:
    issues = [issue.model_dump(mode="json") for issue in report.issues]
    if review is not None:
        issues.extend(issue.model_dump(mode="json") for issue in review.issues)
    return issues


def _failed_group_ids(report: QualityReport, groups: list[QuestionGroup]) -> list[str]:
    group_ids = [group.type.value for group in groups]
    affected = {
        affected_id
        for issue in report.issues
        for affected_id in issue.affected_ids
        if affected_id in group_ids
    }
    return [group_id for group_id in group_ids if not affected or group_id in affected]
