from __future__ import annotations

import json

from ielts_novel.models import Chapter, InsertedTerm, Paragraph, VocabularyItem
from ielts_novel.prompts import SYSTEM_PROMPT, build_user_prompt

DENSITY = {"min_per_500_chars": 20, "target_per_500_chars": 28, "max_per_500_chars": 35}


def _chapter(texts):
    return Chapter(chapter_id=1, chapter_title="第一章", paragraphs=[Paragraph(id=f"1-{index:03d}", text=text) for index, text in enumerate(texts, 1)])


def test_prompt_paragraph_minimums_add_up_to_block_minimum():
    chapter = _chapter(["甲" * 45, "乙" * 28, "丙" * 92, "丁" * 105])
    payload = json.loads(build_user_prompt(chapter, [], [], DENSITY).split("JSON 输入：\n", 1)[1])
    assert sum(item["minimum_items"] for item in payload["paragraphs"]) == payload["minimum_learning_items"]
    assert payload["minimum_learning_items"] == 11


def test_prompt_keeps_paragraph_maximum_above_minimum_for_tiny_paragraphs():
    chapter = _chapter(["甲" * 12, "乙" * 12])
    payload = json.loads(build_user_prompt(chapter, [], [], DENSITY).split("JSON 输入：\n", 1)[1])
    assert all(item["minimum_items"] <= item["maximum_items"] for item in payload["paragraphs"])


def test_prompt_requires_json_and_text_level_consistency():
    assert "JSON" in SYSTEM_PROMPT
    assert "inserted_terms 只能列出 converted_text 里真实出现的英文" in SYSTEM_PROMPT


def test_prompt_includes_candidate_vocabulary_and_retry_note():
    item = VocabularyItem(word="conceal", lemma="conceal", meaning="掩饰", part_of_speech="verb", cefr="B2")
    prompt = build_user_prompt(_chapter(["甲" * 100]), [item], [], DENSITY, retry_note="质量问题：density_too_low")
    assert "conceal" in prompt
    assert "上次输出失败" in prompt


def test_system_prompt_examples_are_valid_json():
    example = SYSTEM_PROMPT.split("JSON 输出完整示例：", 1)[1].strip()
    payload = json.loads(example)
    assert payload["paragraphs"][0]["id"] == "1-001"
    assert InsertedTerm.model_validate(payload["paragraphs"][0]["inserted_terms"][0]).lemma == "conceal"


def test_system_prompt_requires_plan_and_phonetics():
    assert "先写 plan" in SYSTEM_PROMPT
    assert "phonetic" in SYSTEM_PROMPT
    assert "C1" in SYSTEM_PROMPT
    example = json.loads(SYSTEM_PROMPT.split("JSON 输出完整示例：", 1)[1].strip())
    assert example["paragraphs"][0]["plan"][0]["zh"] == "掩饰"
