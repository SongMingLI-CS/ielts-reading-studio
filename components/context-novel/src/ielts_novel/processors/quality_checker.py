from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ielts_novel.models import Chapter, ConvertedChapter, InsertedTerm
from ielts_novel.processors.term_extractor import (
    build_lookup,
    extract_occurrences,
    group_by_sentence,
    merge_lookup,
    paragraph_lookup,
    repair_chapter_stacking,
    sentence_spans,
)

MAXIMUM_ITEMS_PER_SENTENCE = 3


@dataclass
class QualityReport:
    passed: bool
    issues: list[str] = field(default_factory=list)
    metrics: dict[str, float | int] = field(default_factory=dict)


class QualityChecker:
    def __init__(self, minimum_density: float = 20, maximum_density: float = 35, *, allow_discrete_rounding: bool = False):
        self.minimum_density = minimum_density
        self.maximum_density = maximum_density
        self.allow_discrete_rounding = allow_discrete_rounding

    def check(
        self,
        source: Chapter,
        converted: ConvertedChapter,
        *,
        protected_terms: list[str] | None = None,
        lookup: dict[str, InsertedTerm] | None = None,
    ) -> QualityReport:
        issues: list[str] = []
        source_ids = [p.id for p in source.paragraphs]
        output_ids = [p.id for p in converted.paragraphs]
        if len(output_ids) != len(set(output_ids)):
            issues.append("duplicate_paragraph_ids")
        if set(source_ids) != set(output_ids) or len(source_ids) != len(output_ids):
            issues.append("paragraph_ids_mismatch")
        if converted.chapter_id != source.chapter_id:
            issues.append("chapter_id_mismatch")

        source_text = "\n".join(p.text for p in source.paragraphs)
        output_text = "\n".join(p.converted_text for p in converted.paragraphs)
        chinese_chars = len(re.findall(r"[\u3400-\u9fff]", source_text))

        chapter_lookup = build_lookup(term for paragraph in converted.paragraphs for term in paragraph.inserted_terms)
        context = merge_lookup(chapter_lookup, lookup)

        inserted_count = 0
        distinct: set[str] = set()
        for paragraph in converted.paragraphs:
            occurrences = extract_occurrences(paragraph.converted_text, lookup=paragraph_lookup(paragraph, context))
            inserted_count += len(occurrences)
            distinct.update(occurrence.term.lemma.lower() for occurrence in occurrences)
            if any(not occurrence.term.meaning.strip() for occurrence in occurrences):
                issues.append("missing_inline_meaning")
            for term in paragraph.inserted_terms:
                if not re.search(rf"(?<![A-Za-z]){re.escape(term.word)}(?![A-Za-z])", paragraph.converted_text, re.IGNORECASE):
                    issues.append("term_not_in_text")
            for group in group_by_sentence(occurrences, sentence_spans(paragraph.converted_text)):
                if len(group) > MAXIMUM_ITEMS_PER_SENTENCE:
                    issues.append("english_stacking")

        density = inserted_count * 500 / max(chinese_chars, 1)
        if self.allow_discrete_rounding:
            # Inner blocks only need a rounded sanity band: the strict 20-35 band is enforced on
            # the assembled chapter, where integer rounding of short paragraphs cannot distort it.
            minimum_count = math.floor(chinese_chars * self.minimum_density / 500)
            maximum_count = math.ceil(chinese_chars * self.maximum_density / 500) + 2
            if inserted_count < minimum_count:
                issues.append("density_too_low")
            if inserted_count > maximum_count:
                issues.append("density_too_high")
        else:
            if density < self.minimum_density:
                issues.append("density_too_low")
            if density > self.maximum_density:
                issues.append("density_too_high")

        for term in protected_terms or []:
            if term in source_text and term not in output_text:
                issues.append("protected_term_changed")
                break
        source_numbers = Counter(re.findall(r"\d+(?:\.\d+)?", source_text))
        output_numbers = Counter(re.findall(r"\d+(?:\.\d+)?", output_text))
        if source_numbers != output_numbers:
            issues.append("numbers_changed")
        if "```" in output_text or re.search(r"(?m)^\s*(?:解释|说明|总结)[:：]", output_text):
            issues.append("markdown_or_explanation")
        missing_meaning = any(f"{term.word}（" not in paragraph.converted_text for paragraph in converted.paragraphs for term in paragraph.inserted_terms)
        if missing_meaning:
            issues.append("missing_inline_meaning")
        if source_text and len(output_text) < len(source_text) * 0.55:
            issues.append("content_loss")
        output_by_id = {paragraph.id: paragraph for paragraph in converted.paragraphs}
        similarities: list[float] = []
        restored_chinese = 0
        for source_paragraph in source.paragraphs:
            output_paragraph = output_by_id.get(source_paragraph.id)
            if output_paragraph is None:
                continue
            restored = output_paragraph.converted_text
            restored = re.sub(r"[A-Za-z][A-Za-z '\-]+（([^）]+)）", r"\1", restored)
            restored = re.sub(r"[A-Za-z][A-Za-z '\-]*", "", restored)
            restored_chinese += len(re.findall(r"[\u3400-\u9fff]", restored))
            similarities.append(SequenceMatcher(None, source_paragraph.text, restored, autojunk=False).ratio())
        # Inserting English by appending new sentences would silently grow the plot, so the restored
        # Chinese text must stay close to the original length.
        if chinese_chars and restored_chinese > chinese_chars * 1.15:
            issues.append("content_added")
        if similarities and sum(similarities) / len(similarities) < 0.35:
            issues.append("content_similarity_too_low")
        if any(output_text.count(p.converted_text) > 1 for p in converted.paragraphs if len(p.converted_text) > 80):
            issues.append("content_repetition")
        return QualityReport(
            not issues,
            list(dict.fromkeys(issues)),
            {
                "chinese_chars": chinese_chars,
                "inserted_count": inserted_count,
                "distinct_terms": len(distinct),
                "density_per_500": round(density, 2),
            },
        )


def repair_english_stacking(chapter: ConvertedChapter, *, maximum_per_sentence: int = 3, lookup: dict[str, InsertedTerm] | None = None) -> ConvertedChapter:
    """Backwards-compatible wrapper around :func:`term_extractor.repair_chapter_stacking`."""
    return repair_chapter_stacking(chapter, maximum_per_sentence=maximum_per_sentence, lookup=lookup)


__all__ = ["MAXIMUM_ITEMS_PER_SENTENCE", "QualityChecker", "QualityReport", "repair_english_stacking"]

