from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ielts_novel.models import ConvertedChapter, ConvertedParagraph, InsertedTerm

RUN_TOKEN = r"[A-Za-z][A-Za-z'\u2019\-]*"
TOKEN = r"[A-Za-z][A-Za-z'\u2019\-]+"
TOKEN_RE = re.compile(RUN_TOKEN)
ANNOTATED_RE = re.compile(rf"(?P<run>{RUN_TOKEN}(?:[ \t\u00a0]+{RUN_TOKEN})*)[ \t\u00a0]*（(?P<meaning>[^）]{{1,40}})）")
BARE_RE = re.compile(TOKEN)
SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?\n]?")

FUNCTION_WORDS = frozenset({
    "to", "of", "in", "on", "for", "with", "as", "up", "out", "at", "from", "by", "into", "about",
    "be", "off", "over", "down", "than", "and", "or", "so", "not", "it", "that",
})


@dataclass(frozen=True)
class Occurrence:
    term: InsertedTerm
    start: int
    end: int
    raw: str
    annotated: bool


def build_lookup(items: Iterable[InsertedTerm] = ()) -> dict[str, InsertedTerm]:
    """Index vocabulary items by word and lemma so text occurrences can be resolved."""
    lookup: dict[str, InsertedTerm] = {}
    for item in items:
        for key in (item.word, item.lemma):
            normalized = (key or "").strip().lower()
            if normalized:
                lookup.setdefault(normalized, item)
    return lookup


def merge_lookup(*lookups: dict[str, InsertedTerm] | None) -> dict[str, InsertedTerm]:
    """Merge lookup indexes; later arguments win (base vocabulary overrides model declarations)."""
    merged: dict[str, InsertedTerm] = {}
    for lookup in lookups:
        if lookup:
            merged.update(lookup)
    return merged


def sentence_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in SENTENCE_RE.finditer(text) if match.group(0).strip()]


def group_by_sentence(occurrences: list[Occurrence], spans: list[tuple[int, int]]) -> list[list[Occurrence]]:
    groups: list[list[Occurrence]] = [[] for _ in spans]
    for occurrence in occurrences:
        for index, (start, end) in enumerate(spans):
            if start <= occurrence.start < end:
                groups[index].append(occurrence)
                break
    return groups


def _resolve_run(run: str, lookup: dict[str, InsertedTerm], *, annotated: bool) -> tuple[str, int]:
    """Return the term word inside a latin run plus its offset inside that run."""
    tokens = list(TOKEN_RE.finditer(run))
    lowered = [token.group(0).lower() for token in tokens]
    for start in range(len(tokens)):
        candidate = " ".join(lowered[start:])
        if candidate in lookup:
            return candidate, tokens[start].start()
    if annotated and len(tokens) > 1 and lowered[-1] in FUNCTION_WORDS:
        return " ".join(lowered), tokens[0].start()
    return lowered[-1], tokens[-1].start()


def _make_term(word: str, meaning: str, source: InsertedTerm | None) -> InsertedTerm:
    if source is not None:
        return source.model_copy(update={"word": source.word or word, "meaning": meaning or source.meaning})
    return InsertedTerm(
        word=word,
        lemma=word.lower(),
        meaning=meaning,
        part_of_speech="phrase" if " " in word else "unknown",
        cefr="B2",
    )


def extract_occurrences(text: str, *, lookup: dict[str, InsertedTerm] | None = None) -> list[Occurrence]:
    """Derive every English learning item occurrence from the converted text.

    The converted text is the source of truth: items that were only declared by the model but
    never written into the paragraph do not exist for density accounting.
    """
    lookup = lookup or {}
    occurrences: list[Occurrence] = []
    covered: list[tuple[int, int]] = []
    for match in ANNOTATED_RE.finditer(text):
        word, offset = _resolve_run(match.group("run"), lookup, annotated=True)
        start = match.start("run") + offset
        meaning = match.group("meaning").strip()
        occurrences.append(
            Occurrence(
                term=_make_term(word, meaning, lookup.get(word)),
                start=start,
                end=match.end(),
                raw=text[start:match.end()],
                annotated=True,
            )
        )
        covered.append((match.start("run"), match.end()))
    for match in BARE_RE.finditer(text):
        if any(left <= match.start() and match.end() <= right for left, right in covered):
            continue
        word = match.group(0)
        occurrences.append(
            Occurrence(
                term=_make_term(word, "", lookup.get(word.lower())),
                start=match.start(),
                end=match.end(),
                raw=word,
                annotated=False,
            )
        )
    occurrences.sort(key=lambda item: item.start)
    return occurrences


def paragraph_lookup(paragraph: ConvertedParagraph, base: dict[str, InsertedTerm] | None = None) -> dict[str, InsertedTerm]:
    clues = list(paragraph.inserted_terms)
    clues.extend(InsertedTerm(word=item.en, lemma=item.en, meaning=item.zh) for item in paragraph.plan if item.en.strip())
    return merge_lookup(build_lookup(clues), base)


def normalize_chapter(chapter: ConvertedChapter, *, lookup: dict[str, InsertedTerm] | None = None) -> ConvertedChapter:
    """Replace model-declared terms with the items that really appear in each paragraph."""
    paragraphs: list[ConvertedParagraph] = []
    for paragraph in chapter.paragraphs:
        occurrences = extract_occurrences(paragraph.converted_text, lookup=paragraph_lookup(paragraph, lookup))
        paragraphs.append(paragraph.model_copy(update={"inserted_terms": [occurrence.term for occurrence in occurrences]}))
    return chapter.model_copy(update={"paragraphs": paragraphs})


def collect_annotations(chapter: ConvertedChapter) -> dict[str, InsertedTerm]:
    """Chapter-wide annotation index, reused as cross-block context for later chunks."""
    return build_lookup(term for paragraph in chapter.paragraphs for term in paragraph.inserted_terms)


def revert_last_occurrence(paragraph: ConvertedParagraph, occurrences: list[Occurrence], lookup: dict[str, InsertedTerm] | None = None) -> ConvertedParagraph:
    """Restore the last meaning-bearing occurrence of a paragraph back to its Chinese meaning."""
    revertible = [occurrence for occurrence in occurrences if occurrence.term.meaning]
    if not revertible:
        return paragraph
    target = revertible[-1]
    text = paragraph.converted_text[:target.start] + target.term.meaning + paragraph.converted_text[target.end:]
    return paragraph.model_copy(
        update={
            "converted_text": text,
            "inserted_terms": [occurrence.term for occurrence in extract_occurrences(text, lookup=paragraph_lookup(paragraph, lookup))],
        }
    )


def repair_chapter_stacking(
    chapter: ConvertedChapter,
    *,
    maximum_per_sentence: int = 3,
    lookup: dict[str, InsertedTerm] | None = None,
) -> ConvertedChapter:
    """Enforce the per-sentence learning item cap and guarantee one inline meaning per new word.

    Excess items are restored to their Chinese meaning (never dropped silently), kept bare
    occurrences of a brand new word get their `word（释义）` annotation added, and stray latin
    fragments with no known meaning at all (for example leaked pinyin such as `抚mo`) are removed
    so the paragraph stays readable instead of failing the whole chapter.
    """
    repaired: list[ConvertedParagraph] = []
    for paragraph in chapter.paragraphs:
        text = paragraph.converted_text
        index = paragraph_lookup(paragraph, lookup)
        occurrences = extract_occurrences(text, lookup=index)
        if occurrences:
            groups = group_by_sentence(occurrences, sentence_spans(text))
            counters: dict[int, int] = {}
            annotated_words = {occurrence.term.word.lower() for occurrence in occurrences if occurrence.annotated}
            pieces: list[str] = []
            cursor = 0
            for sentence_index, group in enumerate(groups):
                for occurrence in group:
                    count = counters.get(sentence_index, 0)
                    counters[sentence_index] = count + 1
                    pieces.append(text[cursor:occurrence.start])
                    if not occurrence.term.meaning and not occurrence.annotated:
                        pass  # stray fragment: drop the token entirely
                    elif count >= maximum_per_sentence and occurrence.term.meaning:
                        pieces.append(occurrence.term.meaning)
                    elif occurrence.annotated or not occurrence.term.meaning or occurrence.term.word.lower() in annotated_words:
                        pieces.append(occurrence.raw)
                    else:
                        pieces.append(f"{occurrence.raw}（{occurrence.term.meaning}）")
                        annotated_words.add(occurrence.term.word.lower())
                    cursor = occurrence.end
            pieces.append(text[cursor:])
            text = re.sub(r"[ \u00a0\u3000]{2,}", " ", "".join(pieces))
        repaired.append(
            paragraph.model_copy(
                update={
                    "converted_text": text,
                    "inserted_terms": [occurrence.term for occurrence in extract_occurrences(text, lookup=paragraph_lookup(paragraph, lookup))],
                }
            )
        )
    return chapter.model_copy(update={"paragraphs": repaired})


__all__ = [
    "Occurrence",
    "build_lookup",
    "collect_annotations",
    "extract_occurrences",
    "group_by_sentence",
    "merge_lookup",
    "normalize_chapter",
    "paragraph_lookup",
    "repair_chapter_stacking",
    "revert_last_occurrence",
    "sentence_spans",
]
