from __future__ import annotations

import hashlib
import json


def stage_cache_key(
    stage: str,
    source_hash: str,
    difficulty: str,
    question_types: list[str],
    model: str,
    prompt_version: str,
    parameters: dict[str, object],
) -> str:
    """Return a stable SHA-256 key for the semantic inputs of one stage."""
    payload = {
        "stage": stage,
        "source_hash": source_hash,
        "difficulty": difficulty,
        "question_types": question_types,
        "model": model,
        "prompt_version": prompt_version,
        "parameters": parameters,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
