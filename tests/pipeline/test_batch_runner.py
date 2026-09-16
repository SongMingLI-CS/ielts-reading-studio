from __future__ import annotations

import threading
import time

from app.agents.base import ProviderAuthError
from app.models import UnitStatus
from app.pipeline.batch_runner import BatchRunner
from app.pipeline.unit_runner import UnitRunResult


class ConcurrentFakeRunner:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0

    def run(self, unit_id: str, *, job_id: str | None = None):
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(0.01)
        with self.lock:
            self.active -= 1
        return UnitRunResult(unit_id=unit_id, status=UnitStatus.COMPLETED)


def test_batch_uses_bounded_concurrency_and_completes_job(pipeline_app):
    config, repository, store, unit = pipeline_app
    units = [unit]
    for ordinal in range(2, 6):
        changed = unit.model_copy(update={"id": f"u{ordinal}", "ordinal": ordinal})
        repository.add_unit(changed)
        units.append(changed)
    repository.create_job("job-1", "corpus-1", "queued", {"unit_ids": [value.id for value in units]})
    repository.assign_units_to_job([value.id for value in units], "job-1")
    fake = ConcurrentFakeRunner()

    summary = BatchRunner(config, repository, store, fake).run("job-1")

    assert sorted(summary.completed) == ["u1", "u2", "u3", "u4", "u5"]
    assert fake.maximum_active == config.concurrency
    assert repository.get_job("job-1")["status"] == "completed"


def test_paused_job_does_not_claim_units(pipeline_app):
    config, repository, store, unit = pipeline_app
    repository.create_job("job-1", "corpus-1", "paused", {"unit_ids": [unit.id]})
    repository.assign_units_to_job([unit.id], "job-1")
    fake = ConcurrentFakeRunner()

    summary = BatchRunner(config, repository, store, fake).run("job-1")

    assert summary.completed == []
    assert repository.get_unit(unit.id).status == UnitStatus.INDEXED


def test_auth_failure_blocks_job_and_stops_new_claims(pipeline_app):
    config, repository, store, unit = pipeline_app
    units = [unit]
    for ordinal in range(2, 5):
        changed = unit.model_copy(update={"id": f"u{ordinal}", "ordinal": ordinal})
        repository.add_unit(changed)
        units.append(changed)
    repository.create_job("job-1", "corpus-1", "queued", {"unit_ids": [value.id for value in units]})
    repository.assign_units_to_job([value.id for value in units], "job-1")

    class AuthFailureRunner:
        def __init__(self):
            self.calls = 0

        def run(self, unit_id, *, job_id=None):
            self.calls += 1
            raise ProviderAuthError("invalid key")

    fake = AuthFailureRunner()
    summary = BatchRunner(config, repository, store, fake).run("job-1")

    assert summary.blocked
    assert fake.calls <= config.concurrency
    assert repository.get_job("job-1")["status"] == "blocked"
