from __future__ import annotations

import json

from ielts_novel.models import VocabularyItem
from ielts_novel.processors.vocabulary_builder import CATEGORY_RATIOS, CEFR_RATIOS, VocabularyBuilder, build_buckets, fill_phonetics


class FakeProvider:
    """Returns quota-shaped vocabulary batches without touching the network."""

    def __init__(self, *, per_batch: int = 5, fail_keys: set[str] | None = None):
        self.per_batch = per_batch
        self.fail_keys = fail_keys or set()
        self.calls = 0

    def complete_json(self, *, system, user, max_tokens=8192, model=None):
        self.calls += 1
        if "失败" in user:
            raise RuntimeError("boom")
        cefr = user.split("难度为 ")[1][:2]
        category = user.split("category 字段固定为 ")[1].split("，")[0]
        items = [
            {
                "word": f"{category}{cefr.lower()}{index}",
                "lemma": f"{category}{cefr.lower()}{index}",
                "meaning": f"释义{index}",
                "part_of_speech": "phrase" if category in {"collocation", "phrasal_verb"} else category.rstrip("_adverb"),
                "cefr": cefr,
                "phonetic": "/test/",
                "collocation": f"{category} collocation",
                "example_sentence": "This is a short example.",
                "category": category,
            }
            for index in range(self.per_batch)
        ]
        return {"items": items}, 10, 20


def test_build_buckets_follow_requested_ratios():
    buckets = build_buckets(6000)
    total = sum(bucket.target for bucket in buckets)
    assert 5700 <= total <= 6300
    by_category: dict[str, int] = {}
    by_cefr: dict[str, int] = {}
    for bucket in buckets:
        by_category[bucket.category] = by_category.get(bucket.category, 0) + bucket.target
        by_cefr[bucket.cefr] = by_cefr.get(bucket.cefr, 0) + bucket.target
    assert abs(by_cefr["B2"] / total - CEFR_RATIOS["B2"]) < 0.02
    assert abs(by_category["verb"] / total - CATEGORY_RATIOS["verb"]) < 0.02


def test_builder_writes_catalogue_and_report(tmp_path):
    path = tmp_path / "data" / "vocabulary.json"
    builder = VocabularyBuilder(FakeProvider(per_batch=4), path, target=300, batch_size=60, concurrency=2, log=lambda message: None)

    report = builder.build()

    assert path.exists()
    items = json.loads(path.read_text(encoding="utf-8"))
    assert len(items) == len({item["lemma"] for item in items})
    assert all(item["phonetic"] and item["collocation"] and item["example_sentence"] for item in items)
    assert report.accepted >= 15
    assert report.requests >= 15
    assert not report.failures


def test_builder_resumes_from_partial_file(tmp_path):
    path = tmp_path / "data" / "vocabulary.json"
    first = VocabularyBuilder(FakeProvider(per_batch=3), path, target=100, batch_size=50, concurrency=1, log=lambda message: None)
    first.build()
    partial = json.loads(builder_partial(path).read_text(encoding="utf-8"))
    assert partial["done_batches"]

    second = VocabularyBuilder(FakeProvider(per_batch=3), path, target=100, batch_size=50, concurrency=1, log=lambda message: None)
    report = second.build()

    assert report.requests == 0
    assert json.loads(path.read_text(encoding="utf-8"))


def builder_partial(path):
    return path.with_name(path.stem + "_partial.json")


def test_builder_skips_low_value_and_duplicate_items(tmp_path):
    class MixedProvider(FakeProvider):
        def complete_json(self, *, system, user, max_tokens=8192, model=None):
            item, _in, _out = super().complete_json(system=system, user=user, max_tokens=max_tokens, model=model)
            items = list(item["items"])
            items.append({"word": "good", "lemma": "good", "meaning": "好的", "part_of_speech": "adjective", "cefr": "B1"})
            items.append({"word": "nochinese", "lemma": "nochinese", "meaning": "", "part_of_speech": "noun", "cefr": "B1"})
            return {"items": items}, 10, 20

    path = tmp_path / "data" / "vocabulary.json"
    builder = VocabularyBuilder(MixedProvider(per_batch=3), path, target=60, batch_size=60, concurrency=1, log=lambda message: None)

    report = builder.build()

    items = json.loads(path.read_text(encoding="utf-8"))
    assert all(item["lemma"] != "good" for item in items)
    assert all(item["lemma"] != "nochinese" for item in items)
    assert report.rejected >= 2


def test_builder_keeps_going_when_a_batch_fails(tmp_path):
    class FlakyProvider(FakeProvider):
        def __init__(self):
            super().__init__(per_batch=2)
            self.failed = False

        def complete_json(self, *, system, user, max_tokens=8192, model=None):
            if not self.failed:
                self.failed = True
                raise RuntimeError("network")
            return super().complete_json(system=system, user=user, max_tokens=max_tokens, model=model)

    path = tmp_path / "data" / "vocabulary.json"
    builder = VocabularyBuilder(FlakyProvider(), path, target=30, batch_size=30, concurrency=1, log=lambda message: None)

    report = builder.build()

    assert report.failures
    assert report.accepted >= 2


def test_fill_phonetics_only_touches_items_without_phonetic():
    from ielts_novel.processors.vocabulary_builder import fill_phonetics

    class PhoneticProvider:
        def __init__(self):
            self.calls = 0

        def complete_json(self, *, system, user, max_tokens=8192, model=None):
            self.calls += 1
            words = [word.strip() for word in user.split("英式音标：", 1)[1].split("、")]
            return {"items": [{"word": word, "phonetic": f"/{word.lower()}/"} for word in words]}, 10, 20

    items = [
        VocabularyItem(word="gaze", lemma="gaze", meaning="凝视", part_of_speech="verb", cefr="B2", phonetic="/ɡeɪz/"),
        VocabularyItem(word="hush", lemma="hush", meaning="寂静", part_of_speech="noun", cefr="C1"),
    ]
    usage = fill_phonetics(PhoneticProvider(), items, batch_size=10, concurrency=1, log=lambda message: None)

    assert items[0].phonetic == "/ɡeɪz/"
    assert items[1].phonetic == "/hush/"
    assert usage["filled"] == 1
