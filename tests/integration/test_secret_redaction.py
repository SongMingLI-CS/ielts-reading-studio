from __future__ import annotations

from app.agents.base import ModelRequest, ProviderAuthError
from app.agents.deepseek import DeepSeekProvider
from app.config import AppConfig, redact_secrets
from app.models import Difficulty
from app.pipeline.service import ReadingStudioService
from app.planning.units import default_question_types
from tests.agents.test_deepseek import FakeClient, FakeHTTPError
from tests.fixtures.valid_generation import DeterministicProvider


def test_sentinel_key_never_reaches_errors_database_or_outputs(tmp_path, caplog):
    sentinel = "sk-SENTINEL-DO-NOT-LEAK-123456"
    env_path = tmp_path / ".env"
    env_path.write_text(f"DEEPSEEK_API_KEY={sentinel}\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("output_dir: output\ndatabase_path: output/state.db\n", encoding="utf-8")
    config = AppConfig.load(config_path, require_api_key=True)
    source = tmp_path / "source.txt"
    source.write_text(
        "第一章 开始\n" + "正文。" * 300 + "\n第二章 后续\n" + "后文。" * 300,
        encoding="utf-8",
    )
    studio = ReadingStudioService(config, provider=DeterministicProvider())
    corpus = studio.import_source(source).corpus
    result = studio.generate_sample(
        corpus.id,
        Difficulty.STANDARD,
        default_question_types(Difficulty.STANDARD),
    )
    assert result.package is not None
    assert list((config.output_dir / "raw_responses").rglob("*.json"))

    client = FakeClient()
    client.completions.outcomes.append(FakeHTTPError(401, f"Bearer {sentinel}"))
    provider = DeepSeekProvider(config, client=client)
    request = ModelRequest(
        stage="redaction",
        model="fake",
        system='Return JSON such as {"ok": true}.',
        user='JSON input: {"safe": true}',
        max_tokens=10,
    )
    try:
        provider.complete_json(request)
    except ProviderAuthError as exc:
        assert sentinel not in str(exc)
    else:
        raise AssertionError("authentication failure was not raised")

    assert sentinel not in redact_secrets(f"Bearer {sentinel}")
    assert sentinel not in caplog.text
    for path in tmp_path.rglob("*"):
        if not path.is_file() or path == env_path:
            continue
        assert sentinel.encode() not in path.read_bytes(), f"secret leaked to {path}"
