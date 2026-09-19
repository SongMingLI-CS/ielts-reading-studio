"""Cross-cutting security helpers for the web tier and the worker.

Contents:

* :mod:`app.security.sessions` — server-side browser sessions (rotation, expiry).
* :mod:`app.security.csrf` — token validation bound to a session.
* :mod:`app.security.rate_limit` — persistent fixed-window counters on SQLite.
* :mod:`app.security.headers` — response headers and cache policy.
* :mod:`app.security.budget` — server-side size/token budgets.
* :mod:`app.security.client_ip` — trusted-proxy aware client resolution.
* :mod:`app.security.redaction` — log/error scrubbing.
"""
