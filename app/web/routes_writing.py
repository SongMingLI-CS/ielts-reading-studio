from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from starlette.requests import Request

from app.agents.base import AgentSchemaError, ProviderError
from app.config import redact_secrets
from app.pipeline.service import ReadingStudioService
from app.security.budget import BudgetError
from app.writing.models import WritingEvaluationRequest, WritingEvaluationResponse

from .dependencies import get_service
from .templating import templates

router = APIRouter(tags=["writing"])
TEMPLATES = templates()


@router.get("/writing", include_in_schema=False)
def writing_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "writing/index.html")


@router.post("/api/writing/evaluate", response_model=WritingEvaluationResponse)
def evaluate_writing(
    submission: WritingEvaluationRequest,
    service: Annotated[ReadingStudioService, Depends(get_service)],
) -> WritingEvaluationResponse:
    try:
        return service.evaluate_writing(submission)
    except BudgetError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": exc.code, "message": redact_secrets(exc)},
        ) from exc
    except AgentSchemaError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "invalid_model_response", "message": redact_secrets(exc)},
        ) from exc