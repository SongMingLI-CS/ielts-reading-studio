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


def templates() -> Jinja2Templates:
    """A Jinja2Templates instance with the security globals registered."""

    environment = Jinja2Templates(directory=TEMPLATES_DIR)
    environment.env.globals.update(
        csrf_field=csrf_field,
        csrf_token=csrf_token,
        csp_nonce=csp_nonce,
    )
    return environment
