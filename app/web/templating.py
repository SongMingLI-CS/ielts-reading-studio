"""Shared Jinja2 environment so security helpers exist in every template.

Route modules build their ``Jinja2Templates`` through :func:`templates` instead of
constructing their own, which gives all of them the CSRF field helper and the CSP nonce
without touching each render call.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup, escape

TEMPLATES_DIR = Path(__file__).parents[2] / "templates"


def _request_state(context, name: str, default: str = "") -> str:
    request = context.get("request") if hasattr(context, "get") else None
    state = getattr(request, "state", None)
    value = getattr(state, name, default) if state is not None else default
    return value if isinstance(value, str) else default


@pass_context
def csrf_field(context) -> Markup:
    """Hidden input carrying the session's CSRF token."""

    token = _request_state(context, "csrf_token")
    return Markup(f'<input type="hidden" name="csrf_token" value="{escape(token)}">')


@pass_context
def csrf_token(context) -> str:
    return _request_state(context, "csrf_token")


@pass_context
def csp_nonce(context) -> str:
    return _request_state(context, "csp_nonce")


@pass_context
def scholar_url(context) -> str:
    request = context.get("request")
    if request is None:
        return ""
    from .routes_scholar import configured_urls

    urls = configured_urls(request.app.state.service.config)
    return urls[0] if urls else ""


def templates() -> Jinja2Templates:
    """A Jinja2Templates instance with the security globals registered."""

    from .glossary import unit_status_label

    environment = Jinja2Templates(directory=TEMPLATES_DIR)
    environment.env.globals.update(
        csrf_field=csrf_field,
        csrf_token=csrf_token,
        csp_nonce=csp_nonce,
        scholar_url=scholar_url,
        # 任务表的「状态」列要显示人话，模板里不该出现 indexed / needs_review 这类枚举。
        unit_status_label=unit_status_label,
    )
    return environment
