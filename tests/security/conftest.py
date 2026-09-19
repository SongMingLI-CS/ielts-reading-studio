"""Security tests reuse the web fixtures.

The service, the bootstrapped client, the raw (token-less) client and the sample fixtures
all live in ``tests/web/conftest.py``; re-exporting them here keeps one definition of
"a running app under test" for both suites.
"""

from __future__ import annotations

from tests.web.conftest import (  # re-exported as fixtures for this directory
    bootstrap_csrf,
    client,
    completed_unit,
    needs_review_unit,
    raw_client,
    sample_txt,
    vocabulary_unit,
    web_service,
)

__all__ = [
    "bootstrap_csrf",
    "client",
    "completed_unit",
    "needs_review_unit",
    "raw_client",
    "sample_txt",
    "vocabulary_unit",
    "web_service",
]
