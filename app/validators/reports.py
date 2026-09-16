from __future__ import annotations

from typing import Any

from app.models import QualityIssue, QualityReport


class ReportBuilder:
    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.issues: list[QualityIssue] = []

    def add(
        self,
        code: str,
        message: str,
        *,
        affected_ids: list[str] | None = None,
        question_number: int | None = None,
        severity: str = "error",
    ) -> None:
        self.issues.append(
            QualityIssue(
                code=code,
                message=message,
                severity=severity,
                stage=self.stage,
                affected_ids=affected_ids or [],
                question_number=question_number,
            )
        )

    def build(self, **values: Any) -> QualityReport:
        return QualityReport(passed=not self.issues, issues=self.issues, **values)

