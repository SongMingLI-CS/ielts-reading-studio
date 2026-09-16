from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

from app.agents.base import ProviderError
from app.config import AppConfig, redact_secrets
from app.models import GenerationUnit, UnitStatus
from app.planning.estimate import estimate_run
from app.storage.artifacts import ArtifactStore
from app.storage.repositories import Repository

from .unit_runner import UnitRunResult


@dataclass
class BatchRunSummary:
    job_id: str
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)
    blocked: bool = False


class BatchRunner:
    def __init__(
        self,
        config: AppConfig,
        repository: Repository,
        store: ArtifactStore,
        unit_runner: Any,
    ) -> None:
        self.config = config
        self.repository = repository
        self.store = store
        self.unit_runner = unit_runner

    def run(self, job_id: str) -> BatchRunSummary:
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job: {job_id}")
        summary = BatchRunSummary(job_id=job_id)
        if job["status"] in {"paused", "cancelled"}:
            return summary
        self.repository.update_job(job_id, status="running", payload=job["payload"])
        candidates = [
            unit
            for unit in self.repository.list_job_units(job_id)
            if unit.status not in {UnitStatus.COMPLETED, UnitStatus.CANCELLED}
        ]
        candidates = self._within_limits(candidates)
        pending_units = iter(candidates)
        in_flight: dict[Future, GenerationUnit] = {}
        consecutive_failures = 0

        with ThreadPoolExecutor(max_workers=self.config.concurrency) as executor:
            self._fill(executor, in_flight, pending_units, job_id)
            while in_flight:
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in done:
                    unit = in_flight.pop(future)
                    try:
                        result: UnitRunResult = future.result()
                    except ProviderError as exc:
                        summary.failed.append(unit.id)
                        consecutive_failures += 1
                        if exc.stops_queue:
                            summary.blocked = True
                            self.repository.update_job(
                                job_id,
                                status="blocked",
                                payload={**job["payload"], "error": redact_secrets(exc)},
                            )
                    except Exception:  # noqa: BLE001 - unit failures must remain isolated
                        summary.failed.append(unit.id)
                        consecutive_failures += 1
                    else:
                        if result.status == UnitStatus.COMPLETED:
                            summary.completed.append(unit.id)
                            consecutive_failures = 0
                        elif result.status == UnitStatus.NEEDS_REVIEW:
                            summary.needs_review.append(unit.id)
                            consecutive_failures += 1
                        else:
                            summary.failed.append(unit.id)
                            consecutive_failures += 1
                    if len(summary.completed) and len(summary.completed) % 20 == 0:
                        self._write_summary(job_id, summary)
                current = self.repository.get_job(job_id)
                should_stop = (
                    summary.blocked
                    or consecutive_failures >= self.config.max_consecutive_failures
                    or current is None
                    or current["status"] in {"paused", "cancelled", "blocked"}
                )
                if not should_stop:
                    self._fill(executor, in_flight, pending_units, job_id)

        current = self.repository.get_job(job_id)
        if current is not None and current["status"] == "running":
            status = "completed" if not summary.failed and not summary.needs_review else "completed_with_errors"
            self.repository.update_job(job_id, status=status, payload=job["payload"])
        self._write_summary(job_id, summary)
        return summary

    def _fill(
        self,
        executor: ThreadPoolExecutor,
        in_flight: dict[Future, GenerationUnit],
        pending_units,
        job_id: str,
    ) -> None:
        while len(in_flight) < self.config.concurrency:
            try:
                unit = next(pending_units)
            except StopIteration:
                return
            future = executor.submit(self.unit_runner.run, unit.id, job_id=job_id)
            in_flight[future] = unit

    def _within_limits(self, units: list[GenerationUnit]) -> list[GenerationUnit]:
        selected: list[GenerationUnit] = []
        for unit in units[: self.config.max_units_per_run]:
            proposed = [*selected, unit]
            estimate = estimate_run(
                proposed,
                self.config.author_revision_limit,
                self.config.examiner_revision_limit,
            )
            if estimate.maximum_tokens > self.config.max_estimated_tokens_per_run:
                break
            selected.append(unit)
        return selected

    def _write_summary(self, job_id: str, summary: BatchRunSummary) -> None:
        units = self.repository.list_job_units(job_id)
        total = len(units)
        completed = len(summary.completed)
        failed = len(summary.failed) + len(summary.needs_review)
        self.store.write_json(
            f"reports/{job_id}-summary.json",
            {
                "job_id": job_id,
                "unit_count": total,
                "completed": completed,
                "failed": failed,
                "completion_rate": completed / total if total else 0.0,
                "failure_rate": failed / total if total else 0.0,
                "difficulty_distribution": _counts(unit.difficulty.value for unit in units),
                "question_type_distribution": _counts(
                    value.value for unit in units for value in unit.question_types
                ),
                "token_totals": {
                    "input": sum(
                        usage.input_tokens
                        for unit in units
                        for usage in self.repository.list_usage_records(unit.id)
                    ),
                    "output": sum(
                        usage.output_tokens
                        for unit in units
                        for usage in self.repository.list_usage_records(unit.id)
                    ),
                },
            },
        )


def _counts(values) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result
