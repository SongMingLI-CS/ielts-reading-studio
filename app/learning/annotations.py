"""Render original prose with accessible, non-destructive word lookup buttons."""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString
from markupsafe import Markup, escape

from .lexicon import ENTRIES


@dataclass(frozen=True)
class Hint:
    term: str
    chinese: str
    note: str = ""
    quote: str = ""
    source: str = "常用释义"


BASE_HINTS = {}
for entry in ENTRIES.splitlines():
    term, chinese, variants = entry.split("|")
    hint = Hint(term, chinese)
    for spelling in (term, *variants.split(",")):
        if spelling:
            BASE_HINTS[spelling.casefold()] = hint


def term_pattern(terms):
    # Whole words/phrases only; np.array and substrings like 'view' in 'review'
    # must not become independent vocabulary annotations.
    return re.compile(
        r"(?<![\w.])(?:"
        + "|".join(
            r"\s+".join(re.escape(part) for part in t.split())
            for t in sorted(terms, key=len, reverse=True)
        )
        + r")(?!\w)",
        re.IGNORECASE,
    )


BASE_PATTERN = term_pattern(BASE_HINTS)


class ReadingAnnotations:
    def __init__(self, words=()):
        self.hints = dict(BASE_HINTS)
        self.context_terms = {
            " ".join(word["term"].split()).casefold() for word in words
        }
        for word in words:
            self.hints[" ".join(word["term"].split()).casefold()] = Hint(
                word["term"],
                word["chinese"],
                word.get("usage_note", ""),
                word.get("source_quote", ""),
                "本节语境释义",
            )
        self.pattern = term_pattern(self.hints) if words else BASE_PATTERN
        self.found: dict[str, Hint] = {}

    def plain(self, text: str) -> Markup:
        parts = []
        position = 0
        for match in self.pattern.finditer(text):
            spelling = " ".join(match[0].split()).casefold()
            hint = self.hints[spelling]
            key = hint.term.casefold()
            if key in self.found or (
                len(self.found) >= 18 and spelling not in self.context_terms
            ):
                continue
            self.found[key] = hint
            parts.append(escape(text[position : match.start()]))
            attributes = {
                "data-reading-word": match[0],
                "data-chinese": hint.chinese,
                "data-usage-note": hint.note,
                "data-source-quote": hint.quote,
                "data-meaning-source": hint.source,
                "title": f"{match[0]}：{hint.chinese}",
                "aria-label": f"{match[0]}，查看中文释义",
                "aria-haspopup": "dialog",
                "aria-expanded": "false",
            }
            attrs = " ".join(
                f'{name}="{escape(value)}"' for name, value in attributes.items()
            )
            parts.append(
                Markup(
                    f'<button type="button" class="learn-word-hit" {attrs}>{escape(match[0])}</button>'
                )
            )
            position = match.end()
        parts.append(escape(text[position:]))
        return Markup("").join(parts)

    def markdown(self, rendered: str) -> Markup:
        soup = BeautifulSoup(rendered, "html.parser")
        for node in list(soup.find_all(string=True)):
            if not isinstance(node, NavigableString) or node.find_parent(
                ["pre", "code", "a", "button"]
            ):
                continue
            replacement = BeautifulSoup(str(self.plain(str(node))), "html.parser")
            node.replace_with(*list(replacement.contents))
        return Markup(str(soup))
