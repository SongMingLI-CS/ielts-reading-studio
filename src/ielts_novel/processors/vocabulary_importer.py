from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

POS_TOKENS = "adj|adv|abbr|prep|conj|pron|excl|art|aux|phr|vt|vi|ad|phr|pl|int|num|n|v|a"
POS_MAP = {
    "n": "noun",
    "v": "verb",
    "vt": "verb",
    "vi": "verb",
    "adj": "adjective",
    "a": "adjective",
    "adv": "adverb",
    "ad": "adverb",
}
POS_PERIOD = r"[.．]"
POS_ATOM = rf"(?:{POS_TOKENS})"
POS_RUN = rf"{POS_ATOM}{POS_PERIOD}(?:\s*[/&，,、]\s*{POS_ATOM}{POS_PERIOD})*"
POS_PATTERN = re.compile(rf"^(?P<pos>{POS_RUN})\s*(?P<meaning>.*)$")
POS_ANY = re.compile(rf"(?P<pos>{POS_ATOM}){POS_PERIOD}")
POS_BARE = re.compile(rf"(?P<pos>{POS_ATOM})", re.IGNORECASE)
WORD_TOKEN = r"[A-Za-z][A-Za-z'\u2019\-]*"
WORD_LINE = re.compile(rf"^{WORD_TOKEN}(?:\s+{WORD_TOKEN})*$")
INLINE_ENTRY = re.compile(rf"^(?P<word>{WORD_TOKEN}(?:\s+{WORD_TOKEN})?)\s+(?P<pos>{POS_RUN})\s*(?P<meaning>[\u3400-\u9fff（(].*)$")
MARKER_WORDS = "同|例|记|反|派|搭|辨|用|考|联想|扩展|搭配|例句|词组"
INLINE_MARKER = re.compile(rf"^(?P<marker>{MARKER_WORDS})[\s\u00a0\u3000]+(?P<rest>.*)$")
STANDALONE_MARKER = re.compile(rf"^(?P<marker>{MARKER_WORDS})$")
PHONETIC_LINE = re.compile(r"^[\[［].*[\]］]$")
PHONETICISH = re.compile(r"^英?\s*[\[［][^\]］]+[\]］](?:\s*美\s*[\[［][^\]］]+[\]］])?$")
PHONETIC_VALUE = re.compile(r"[\[［](?P<value>[^\]］]+)[\]］]")
HEADER_NOISE = re.compile(r"^(?:\*+|MP3-\d+|Word\s+List\s*\d+|List\s*\d+|.*预习表)$", re.IGNORECASE)
DERIVATIVE = re.compile(
    rf"(?P<word>{WORD_TOKEN}(?:\s+{WORD_TOKEN})?)\s+(?P<pos>{POS_TOKENS}){POS_PERIOD}\s*(?P<meaning>[\u3400-\u9fff][^\s;；]*)"
    rf"|(?P<word2>{WORD_TOKEN}(?:\s+{WORD_TOKEN})?)\s*[（(](?P<pos2>{POS_TOKENS}){POS_PERIOD}\s*(?P<meaning2>[\u3400-\u9fff][^）)]*)[）)]"
)


@dataclass
class ImportedEntry:
    word: str
    meaning: str
    part_of_speech: str = ""
    category: str = ""
    phonetic: str = ""
    example_sentence: str = ""
    collocation: str = ""
    synonyms: list[str] = field(default_factory=list)
    topic: str = ""

    @property
    def lemma(self) -> str:
        return self.word.strip().lower()


def _clean(line: str) -> str:
    return re.sub(r"[\u00a0\u3000\t]+", " ", line).strip()


def _is_chinese_only(line: str) -> bool:
    return bool(line) and not re.search(r"[A-Za-z]", line) and bool(re.search(r"[\u3400-\u9fff]", line))


def _part_of_speech(pos_run: str, fallback_text: str = "") -> str:
    tokens = POS_ANY.findall(pos_run) or POS_BARE.findall(pos_run)
    for token in tokens:
        mapped = POS_MAP.get(token.lower(), "")
        if mapped:
            return mapped
    if fallback_text:
        for token in POS_ANY.findall(fallback_text) or POS_BARE.findall(fallback_text):
            mapped = POS_MAP.get(token.lower(), "")
            if mapped:
                return mapped
    return ""


def _categorise(word: str, pos: str) -> str:
    if " " in word.strip():
        return "phrasal_verb" if pos in {"verb", ""} else "collocation"
    if pos in {"adjective", "adverb"}:
        return "adjective_adverb"
    if pos in {"verb", "noun"}:
        return pos
    return ""


def _format_phonetic(line: str) -> str:
    # IELTS uses British pronunciation, so the 英 phonetic wins when a book lists both.
    match = re.search(r"英\s*[\[［](?P<value>[^\]］]+)[\]］]", line) or PHONETIC_VALUE.search(line)
    if not match:
        return ""
    return f"/{match.group('value').strip().strip('/').replace(chr(39), '\u02c8')}/"


def normalise_markers(text: str) -> str:
    """Split inline 同/例/记 markers onto their own lines (one of the two books groups them)."""
    return re.sub(rf"[ \u00a0]+(?={MARKER_WORDS}[ \u00a0\u3000])", "\n", text)


def _is_phonetic_line(line: str) -> bool:
    return bool(PHONETIC_LINE.match(line) or PHONETICISH.match(line))


def _entry_start(lines: list[str], index: int) -> bool:
    """A line starts a block entry when a word line is followed by a phonetic or POS line."""
    if not WORD_LINE.match(lines[index]):
        return False
    lookahead = index + 1
    while lookahead < len(lines) and (not lines[lookahead] or HEADER_NOISE.match(lines[lookahead])):
        lookahead += 1
    if lookahead >= len(lines):
        return False
    following = lines[lookahead]
    return bool(POS_PATTERN.match(following)) or _is_phonetic_line(following)


def _read_block(lines: list[str], start: int) -> tuple[str, int]:
    """Collect marker content until the next entry, marker, heading or blank line."""
    collected: list[str] = []
    cursor = start
    while cursor < len(lines):
        candidate = lines[cursor]
        if not candidate:
            break
        if HEADER_NOISE.match(candidate) or STANDALONE_MARKER.match(candidate) or INLINE_MARKER.match(candidate):
            break
        if DERIVATIVE.match(candidate) and not collected:
            collected.append(candidate)
            cursor += 1
            continue
        if INLINE_ENTRY.match(candidate) or _entry_start(lines, cursor):
            break
        collected.append(candidate)
        cursor += 1
    return " ".join(collected), cursor


def _example_from(text: str) -> str:
    english = re.sub(r"【[^】]*】", "", text)
    english = re.split(r"[\u3400-\u9fff]", english, maxsplit=1)[0]
    return re.sub(r"\s+", " ", english).strip()


def _collocation_from(text: str) -> str:
    """Keep the first few English collocations from a 搭 block, dropping the Chinese glosses."""
    phrases = [phrase.strip(" ;；,") for phrase in re.split(r"[;；]", text) if re.search(r"[A-Za-z]", phrase)]
    cleaned = []
    for phrase in phrases:
        english = re.split(r"[\u3400-\u9fff]", phrase, maxsplit=1)[0].strip(" ;；,")
        if 2 < len(english) <= 40:
            cleaned.append(english)
        if len(cleaned) == 3:
            break
    return "; ".join(cleaned)


def parse_vocabulary_text(text: str, *, known_topics: set[str] | None = None) -> list[ImportedEntry]:
    """Parse a vocabulary book's word list into structured entries.

    Two layouts are supported: 「单词 → 音标 → 词性. 释义」 blocks (环球雅思版) and
    「单词 → ［音标］ → 词性. 释义」 with 记/搭/例/派 blocks (俞敏洪乱序版), plus the
    one-line 「word POS. 释义」 preview tables and derivative lists of both books.
    """
    lines = [_clean(line) for line in normalise_markers(text).splitlines()]
    entries: list[ImportedEntry] = []
    topic = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or HEADER_NOISE.match(line) or STANDALONE_MARKER.match(line):
            index += 1
            continue
        if _is_chinese_only(line):
            if not topic or line in (known_topics or set()):
                topic = line
            index += 1
            continue
        inline = INLINE_ENTRY.match(line)
        if inline:
            entry = _build_entry(inline.group("word"), inline.group("pos"), inline.group("meaning"), "", topic)
            index += 1
            entries.append(entry)
            continue
        if _entry_start(lines, index):
            outcome = _read_entry(lines, index, topic)
            if outcome:
                entry, index, derived = outcome
                entries.append(entry)
                entries.extend(derived)
            continue
        index += 1
    return entries


def _build_entry(word: str, pos_run: str, meaning: str, phonetic: str, topic: str) -> ImportedEntry:
    pos = _part_of_speech(pos_run, meaning)
    entry = ImportedEntry(
        word=word.strip(),
        meaning=meaning.strip(),
        part_of_speech=pos,
        phonetic=_format_phonetic(phonetic),
        topic=topic,
    )
    entry.category = _categorise(entry.word, pos)
    return entry


def _read_entry(lines: list[str], index: int, topic: str) -> tuple[ImportedEntry | None, int]:
    word = lines[index]
    cursor = index + 1
    while cursor < len(lines) and (not lines[cursor] or HEADER_NOISE.match(lines[cursor])):
        cursor += 1
    phonetic = ""
    if cursor < len(lines) and _is_phonetic_line(lines[cursor]):
        phonetic = lines[cursor]
        cursor += 1
        while cursor < len(lines) and not lines[cursor]:
            cursor += 1
    if cursor >= len(lines) or not POS_PATTERN.match(lines[cursor]):
        return None
    match = POS_PATTERN.match(lines[cursor])
    entry = _build_entry(word, match.group("pos"), match.group("meaning"), phonetic, topic)
    cursor += 1
    derived: list[ImportedEntry] = []
    while cursor < len(lines):
        candidate = lines[cursor]
        if not candidate:
            cursor += 1
            continue
        marker = INLINE_MARKER.match(candidate) or STANDALONE_MARKER.match(candidate)
        if marker:
            name = marker.group("marker")
            inline_text = marker.groupdict().get("rest") or ""
            block, next_cursor = _read_block(lines, cursor + 1)
            content = (inline_text + " " + block).strip()
            if name == "同":
                entry.synonyms = [item.strip(" ;；") for item in re.split(r"[;；]", content) if item.strip(" ;；")]
            elif name in {"例", "例句"} and not entry.example_sentence:
                entry.example_sentence = _example_from(content)
            elif name in {"搭", "搭配"} and not entry.collocation:
                entry.collocation = _collocation_from(content)
            elif name in {"派", "扩展", "词组"}:
                derived.extend(_derived_entries(content, topic))
                if not block:
                    derived.extend(_derived_entries(inline_text, topic))
            cursor = next_cursor
            continue
        if INLINE_ENTRY.match(candidate) or _entry_start(lines, cursor):
            break
        if _is_chinese_only(candidate):
            cursor += 1
            continue
        cursor += 1
    return entry, cursor, derived


def _derived_entries(content: str, topic: str) -> list[ImportedEntry]:
    entries: list[ImportedEntry] = []
    for word, pos_run, meaning in parse_derivative_line(content):
        entry = _build_entry(word, pos_run, meaning, "", topic)
        if entry.part_of_speech:
            entries.append(entry)
    return entries


def read_epub_lines(path: str | Path) -> tuple[str, set[str]]:
    """Read every document of a vocabulary EPUB as plain text plus its topic headings."""
    import ebooklib
    from bs4 import BeautifulSoup
    from ebooklib import epub

    book = epub.read_epub(str(path), options={"ignore_ncx": True})
    documents = [item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT]
    pages = [BeautifulSoup(item.get_content(), "html.parser").get_text("\n", strip=True) for item in documents]
    topics: set[str] = set()
    for page in pages:
        for raw in page.splitlines():
            line = _clean(raw).strip("• ")
            if "•" in raw:
                topics.update(part.strip("• ") for part in raw.split("•") if 1 < len(part.strip("• ")) <= 6)
            elif _is_chinese_only(line) and 1 < len(line) <= 6:
                topics.add(line)
    return "\n".join(pages), topics


def import_vocabulary_epub(path: str | Path) -> list[ImportedEntry]:
    text, topics = read_epub_lines(path)
    return parse_vocabulary_text(text, known_topics=topics)


def entries_to_catalogue(entries: list[ImportedEntry], *, default_level: str = "B2") -> list:
    """Convert parsed book entries into VocabularyItem rows, dropping function words."""
    from ielts_novel.models import VocabularyItem

    catalogue: dict[str, VocabularyItem] = {}
    for entry in entries:
        if not entry.category or len(entry.word) < 3 or not entry.meaning:
            continue
        if entry.lemma in catalogue:
            current = catalogue[entry.lemma]
            if not current.phonetic and entry.phonetic:
                catalogue[entry.lemma] = current.model_copy(update={"phonetic": entry.phonetic})
            continue
        catalogue[entry.lemma] = VocabularyItem(
            word=entry.word,
            lemma=entry.lemma,
            meaning=entry.meaning,
            part_of_speech=entry.part_of_speech or "phrase",
            cefr=default_level,
            phonetic=entry.phonetic,
            category=entry.category,
            collocation=entry.collocation,
            example_sentence=entry.example_sentence,
        )
    return list(catalogue.values())



def parse_derivative_line(line: str) -> list[tuple[str, str, str]]:
    """Extract 「word POS. 释义」 or 「word（POS. 释义）」 pairs from 派/预习表 lines."""
    results: list[tuple[str, str, str]] = []
    for match in DERIVATIVE.finditer(line):
        if match.group("word"):
            results.append((match.group("word").strip(), match.group("pos"), match.group("meaning").strip()))
        else:
            results.append((match.group("word2").strip(), match.group("pos2"), match.group("meaning2").strip()))
    return results
