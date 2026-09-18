"""题目相似度比较。

生成的练习越多，越容易出现"换汤不换药"的题：同一篇里两组题问同一件事，
或者新篇目的题目和几个月前某篇几乎一模一样。这里只做纯计算，不碰数据库，
方便在质检门禁（validator）和网页审阅页复用。

比较方式用「实词集合的 Jaccard 相似度」：先去掉功能词、统一大小写、切词，
再看两个文本共有的实词比例。它对改写过的同义问法（"What does the author
suggest about X" vs "What is suggested about X"）依然敏感，又不会被语序和
标点影响。
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9'’-]*")

_STOPWORD_GROUPS = (
    "a an the and or but if then than that this these those there here",
    "of in on at to for from with without by as is are was were be been being",
    "do does did done have has had having will would shall should can could may",
    "might must not no nor so such it its they them their he she his her him",
    "you your we our us i me my mine which who whom whose what when where why",
    "how all any both each few more most other some only own same too very",
    "according following below above passage author writer text choose select",
    "write answer answers one two three four five first second question questions",
    "list complete sentence sentences",
)

# 只保留实词：这些词在题干里反复出现，考不出重合度。
STOPWORDS = frozenset(" ".join(_STOPWORD_GROUPS).split())

# 题目相似度阈值。0.72 只拦下"几乎同一道题"，不会因为同主题误伤。
DEFAULT_THRESHOLD = 0.72
# 同篇内部用更严格的阈值：同篇出现两道高度重合的题几乎总是冗余。
WITHIN_UNIT_THRESHOLD = 0.8
# 审阅页用的宽松阈值：宁可多列出来给人看，也不在生成时误拦。
REPORT_THRESHOLD = 0.5
# 至少要共有的实词数量：防止短题干只因用词相近就被判重。
MIN_SHARED_TOKENS = 3
# 题干太短（如 "Paragraph A"）时不做判重：信息量不足，误判代价比漏判更高。
MIN_COMPARABLE_TOKENS = 5


def normalise(text: str) -> str:
    return " ".join(TOKEN_PATTERN.findall((text or "").casefold()))


def content_tokens(text: str) -> set[str]:
    """实词集合：丢掉功能词和单字母，保留数字（题号、年份常常是唯一区别）。"""
    tokens = {token for token in normalise(text).split() if token not in STOPWORDS}
    return {token for token in tokens if len(token) > 1 or not token.isalpha()}


def _shared(left: str, right: str) -> tuple[float, int, int]:
    left_tokens = content_tokens(left)
    right_tokens = content_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0, 0, min(len(left_tokens), len(right_tokens))
    shared = left_tokens & right_tokens
    return (
        len(shared) / len(left_tokens | right_tokens),
        len(shared),
        min(len(left_tokens), len(right_tokens)),
    )


def similarity(left: str, right: str) -> float:
    """两个文本的实词 Jaccard 相似度，范围 0–1。"""
    return _shared(left, right)[0]


def is_duplicate(
    left: str,
    right: str,
    *,
    threshold: float,
    min_shared: int = MIN_SHARED_TOKENS,
    min_comparable: int = MIN_COMPARABLE_TOKENS,
) -> tuple[bool, float]:
    """判定两个文本是否算重复题，返回 (是否重复, 相似度)。

    除了阈值还要满足两个下限，避免误判：

    - 两边共有的实词至少 ``min_shared`` 个：否则像 "Judge claim 5" 与
      "Judge claim 6" 这种只差编号的短题干会被判成同一题。
    - 两边各自的实词都不少于 ``min_comparable`` 个：题干太短、信息量不足时
      不做判重（漏判由审阅页兜底，误判要花钱重生成）。
    """
    score, shared, smaller = _shared(left, right)
    duplicate = (
        score >= threshold and shared >= min_shared and smaller >= min_comparable
    )
    return duplicate, score


@dataclass(frozen=True)
class QuestionStem:
    """比对用的题目摘要：题干 + 答案 + 题型，可带上来源单元。"""

    unit_id: str
    number: int
    group_type: str
    prompt: str
    answer: str = ""
    unit_ordinal: int | None = None
    unit_title: str | None = None

    @property
    def text(self) -> str:
        return self.prompt or ""

    @property
    def label(self) -> str:
        title = self.unit_title or self.unit_id
        return f"{title} · 第 {self.number} 题"

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "number": self.number,
            "group_type": self.group_type,
            "prompt": self.prompt,
            "answer": self.answer,
            "unit_ordinal": self.unit_ordinal,
            "unit_title": self.unit_title,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> QuestionStem:
        return cls(
            unit_id=payload["unit_id"],
            number=int(payload["number"]),
            group_type=payload.get("group_type", ""),
            prompt=payload.get("prompt", ""),
            answer=payload.get("answer", ""),
            unit_ordinal=payload.get("unit_ordinal"),
            unit_title=payload.get("unit_title"),
        )


@dataclass(frozen=True)
class SimilarPair:
    left: QuestionStem
    right: QuestionStem
    score: float

    @property
    def key(self) -> str:
        return ":".join(
            sorted(
                (
                    f"{self.left.unit_id}#{self.left.number}",
                    f"{self.right.unit_id}#{self.right.number}",
                )
            )
        )

    def describe(self) -> str:
        return (
            f"#{self.left.number} 与 {self.right.unit_title or self.right.unit_id} "
            f"#{self.right.number} 相似度 {self.score:.0%}"
        )


def stems_from_groups(
    unit_id: str,
    groups: Iterable[Any],
    *,
    ordinal: int | None = None,
    title: str | None = None,
) -> list[QuestionStem]:
    return [
        QuestionStem(
            unit_id=unit_id,
            number=int(question.number),
            group_type=str(getattr(group.type, "value", group.type)),
            prompt=question.prompt,
            answer=question.answer,
            unit_ordinal=ordinal,
            unit_title=title,
        )
        for group in groups
        for question in group.questions
    ]


def stems_from_package(package: Any) -> list[QuestionStem]:
    return stems_from_groups(
        package.unit.id,
        package.question_groups,
        ordinal=package.unit.ordinal,
        title=package.passage.title,
    )


@dataclass
class QuestionIndex:
    """已完成篇目的题干索引，支持边跑边追加（批任务里会持续变长）。"""

    threshold: float = DEFAULT_THRESHOLD
    stems: list[QuestionStem] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __len__(self) -> int:
        return len(self.stems)

    def stems_excluding(self, unit_id: str | None) -> list[QuestionStem]:
        """除指定单元外的全部题干快照。"""
        with self._lock:
            return [stem for stem in self.stems if stem.unit_id != unit_id]

    def add(self, stems: Sequence[QuestionStem]) -> None:
        with self._lock:
            self.stems.extend(stems)

    def add_package(self, package: Any) -> None:
        self.add(stems_from_package(package))

    def match(
        self,
        stem: QuestionStem,
        *,
        threshold: float | None = None,
        skip_unit_id: str | None = None,
    ) -> list[SimilarPair]:
        """与索引内已有题目的相似结果，按分数从高到低。"""
        limit = self.threshold if threshold is None else threshold
        with self._lock:
            candidates = list(self.stems)
        found: list[SimilarPair] = []
        for candidate in candidates:
            if candidate.unit_id == skip_unit_id:
                continue
            duplicate, score = is_duplicate(stem.text, candidate.text, threshold=limit)
            if duplicate:
                found.append(SimilarPair(stem, candidate, score))
        return sorted(found, key=lambda pair: pair.score, reverse=True)

    def duplicate_pairs(self, *, threshold: float | None = None) -> list[SimilarPair]:
        """索引内部跨篇的近似重复对，每道题只报最相似的那一次。"""
        limit = self.threshold if threshold is None else threshold
        with self._lock:
            stems = list(self.stems)
        pairs: list[SimilarPair] = []
        seen: set[str] = set()
        for index, stem in enumerate(stems):
            best: SimilarPair | None = None
            for other in stems[index + 1 :]:
                if other.unit_id == stem.unit_id:
                    continue
                duplicate, score = is_duplicate(stem.text, other.text, threshold=limit)
                if duplicate and (best is None or score > best.score):
                    best = SimilarPair(stem, other, score)
            if best is None or best.key in seen:
                continue
            seen.add(best.key)
            pairs.append(best)
        return sorted(pairs, key=lambda pair: pair.score, reverse=True)
