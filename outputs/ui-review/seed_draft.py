"""给隔离预览库种一条真实草稿，用来实测首页"继续上次练习"卡（不影响 output/state.db）。

``uv run python outputs/ui-review/seed_draft.py``
"""

from __future__ import annotations

from pathlib import Path

from app.config import AppConfig
from app.models import UnitStatus
from app.pipeline.service import ReadingStudioService

ROOT = Path(__file__).resolve().parents[2]
REVIEW = Path(__file__).parent
DATA = REVIEW / "preview-data"
DB = DATA / "state.db"
ATTEMPT_ID = "ui-review-draft"
ANSWERED = 7

config = AppConfig(base_dir=REVIEW, input_dir=DATA / "input", output_dir=DATA, database_path=DB)
service = ReadingStudioService(config)

units = [
    (corpus, unit)
    for corpus in service.repository.list_corpora()
    for unit in service.repository.list_units(corpus.id)
    if unit.status == UnitStatus.COMPLETED
]
if not units:
    raise SystemExit("预览库里没有已完成的单元，先跑一次生成流程")

corpus, unit = units[0]
package = service.load_package(unit.id)
numbers = [
    question.number for group in package.question_groups for question in group.questions
]
answers = {str(number): "ui review placeholder" for number in numbers[:ANSWERED]}
service.repository.save_practice_attempt(
    ATTEMPT_ID,
    unit.id,
    status="in_progress",
    payload={"answers": answers, "elapsed_seconds": 504},
)
print(f"已种下草稿：{corpus.name} / {package.passage.title}")
print(f"  已答 {len(answers)} / {len(numbers)} 题，用时 504 秒，attempt_id={ATTEMPT_ID}")
