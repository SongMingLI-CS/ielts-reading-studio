from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ielts_novel.models import VocabularyItem


class VocabularyCatalogError(ValueError):
    pass


LOW_VALUE = {"woman", "man", "good", "go", "bad", "nice", "thing"}
CEFR_RATIOS = {"B1": 0.20, "B2": 0.60, "C1": 0.20}
CATEGORY_RATIOS = {"verb": 0.25, "adjective_adverb": 0.25, "noun": 0.20, "collocation": 0.20, "phrasal_verb": 0.10}


@dataclass(frozen=True)
class VocabularyPlan:
    new: tuple[VocabularyItem, ...]
    review: tuple[VocabularyItem, ...]


def validate_catalog(path: str | Path, *, require_full: bool = False) -> list[VocabularyItem]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = [VocabularyItem.model_validate(item) for item in raw]
    unique = {item.lemma.lower() for item in items}
    if len(unique) != len(items):
        raise VocabularyCatalogError("词库 lemma 必须唯一")
    if require_full and not 5000 <= len(items) <= 7000:
        raise VocabularyCatalogError("正式词库必须包含 5000–7000 个唯一词条")
    return items


class VocabularySelector:
    def __init__(self, items: list[VocabularyItem]):
        if any(item.lemma.lower() in LOW_VALUE for item in items):
            raise VocabularyCatalogError("词库包含低价值基础词")
        self.items = tuple(sorted(items, key=lambda item: (item.cefr, item.part_of_speech, item.lemma)))

    @staticmethod
    def new_ratio(chapter_id: int) -> float:
        return 0.6 if chapter_id <= 100 else 0.4 if chapter_id <= 400 else 0.2

    def select(self, chapter_id: int, count: int, *, seen_lemmas: set[str]) -> VocabularyPlan:
        new_count = round(count * self.new_ratio(chapter_id))
        review_count = count - new_count
        new_pool = [item for item in self.items if item.lemma not in seen_lemmas]
        review_pool = [item for item in self.items if item.lemma in seen_lemmas]
        if len(review_pool) < review_count:
            new_count += review_count - len(review_pool)
            review_count = len(review_pool)
        if len(new_pool) < new_count:
            review_count += new_count - len(new_pool)
            new_count = len(new_pool)
        if len(new_pool) < new_count or len(review_pool) < review_count:
            raise VocabularyCatalogError("可用的新词或复习词数量不足")
        return VocabularyPlan(tuple(self._balanced(new_pool, new_count, chapter_id)), tuple(review_pool[:review_count]))

    @staticmethod
    def _balanced(pool: list[VocabularyItem], count: int, chapter_id: int) -> list[VocabularyItem]:
        """Sample the pool so each chapter keeps the target CEFR and category mix.

        Buckets are consumed round-robin, so every short window of the resulting plan (the window
        that a single paragraph block receives) still mixes difficulty levels and word types. The
        offset derives from the chapter id, which keeps selection deterministic and reproducible
        while spreading the catalogue over a long novel.
        """
        if count >= len(pool):
            return pool
        buckets: list[list[VocabularyItem]] = []
        used: set[str] = set()
        for category in CATEGORY_RATIOS:
            for cefr, cefr_ratio in CEFR_RATIOS.items():
                bucket = [item for item in pool if item.cefr == cefr and item.category == category]
                if bucket:
                    offset = (chapter_id * max(1, round(count * cefr_ratio))) % len(bucket)
                    buckets.append(bucket[offset:] + bucket[:offset])
        chosen: list[VocabularyItem] = []
        while len(chosen) < count and any(buckets):
            for bucket in buckets:
                if len(chosen) >= count:
                    break
                while bucket:
                    item = bucket.pop(0)
                    if item.lemma not in used:
                        chosen.append(item)
                        used.add(item.lemma)
                        break
        for item in pool:
            if len(chosen) >= count:
                break
            if item.lemma not in used:
                chosen.append(item)
                used.add(item.lemma)
        return chosen[:count]
