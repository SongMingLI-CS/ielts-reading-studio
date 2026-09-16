from fastapi import Request

from app.pipeline.service import ReadingStudioService


def get_service(request: Request) -> ReadingStudioService:
    return request.app.state.service
