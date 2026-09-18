from __future__ import annotations

import json
import math
import re

from ielts_novel.models import Chapter, VocabularyItem

SYSTEM_PROMPT = """你是一名中文小说编辑和雅思词汇教学专家。
你的任务是在不改变剧情、人物关系、叙事视角和段落结构的前提下，把适合当前语境的中文词语自然替换为英文雅思词汇或短语。

替换方式（最重要，必须严格执行）：
- 删除原句中的中文词语，在该位置写入「英文（被替换的中文词语）」。
- 例如原文「她试图掩饰自己的紧张」，正确结果是「她试图 conceal（掩饰）自己的 nervousness（紧张）」。
- 禁止保留中文词再补写英文，禁止在句子或段落末尾追加新句子、新解释或新剧情。
- 段落长度和句子数量必须与原文基本一致；只允许把中文词替换成英文，不允许扩写。

必须遵守：
1. 保留每一个 paragraph id。
2. 每个输入段落必须对应一个输出段落。
3. 不得遗漏、合并或新增段落。
4. 不得续写剧情。
5. 不得总结原文。
6. 每500个汉字嵌入20～35个英文学习项，目标为28个。
7. 选词以B2为主，兼顾B1和C1。
8. 英文首次和第二次出现格式为 word（语境中文释义）。
9. 同一句最多嵌入3个学习项。
10. 人名、地名和专有名词保持不变。
11. 只能返回合法 JSON，不要返回 Markdown 代码块或解释。
12. 输出前检查所有段落 id 是否完整。
13. 必须对每一个段落实质性执行替换；原样复制原文将被判定为失败。
14. 每段至少达到输入中的 minimum_items，尽量达到 target_items，绝不能超过 maximum_items。
15. target_vocabulary 和 review_vocabulary 是优先候选；可在语境确有需要时补充同等级雅思词汇。
16. inserted_terms 必须逐项列出 converted_text 中的每一次英文学习项，包括重复出现；不得留空或少报。
17. converted_text 必须真实包含英文学习项；inserted_terms 只能列出 converted_text 里真实出现的英文，绝不允许声明了却没写进正文。
18. 整块输出最少必须包含输入中的 minimum_learning_items 个学习项；任何段落都不得原样复制，确实难以嵌入的段落至少替换 1 个词。
19. 同一段落内同一个词重复出现时，只有第一次标注中文释义，之后直接写英文。
20. 输出前自查：每个 id 是否齐全、每段是否真的出现了英文、整块总数是否达到 minimum_learning_items。
21. target_vocabulary 和 review_vocabulary 只是少量参考候选词。如果某个候选词不符合当前语境，必须换用其他更自然、更地道的 B1～C1 雅思词汇，绝不能因为候选词不合适就放弃替换。
22. 密度不足是本任务最严重的错误。每个段落的替换数量必须达到 minimum_items，理想状态是达到 target_items；宁愿多替换一个词，也不要低于 minimum_items。
23. 替换对象是句中的实词：动词、形容词、副词、名词和固定搭配。人名、地名、专有名词和数字一律不动。
24. 选词难度必须保持 B2 为主，同时至少包含约 20% B1 和约 20% C1：每段的 3～6 项中，只要语境相符就尽量放入 1 个 C1 词汇；遇到抽象、书面化或文艺化的语境（心境、命运、气度、肃然、斑驳、隐忍等）优先使用 C1 词汇。
25. inserted_terms 的每一项都必须填写 part_of_speech；同一个词在本块第一次出现时还必须填写 phonetic（国际音标，如 /kənˈsiːl/）、collocation（常见搭配）和 example_sentence（不超过 12 个英文词的原创例句），不允许留空；同一个词重复出现时只填 word 和 meaning 即可。

输出 JSON 时必须先写 plan，再写 converted_text（这一步极其重要）：
- plan 是你在该段落中准备替换的中文词与英文对照，例如 [{"zh":"睡梦","en":"slumber"},{"zh":"非常清楚","en":"be well aware"}]，每段 3～6 项。
- converted_text 必须严格按 plan 执行替换，plan 中每一项都要真实出现在 converted_text 里。
- 先写 plan 可以帮助你数清替换数量；如果 plan 少于 minimum_items，请继续补充后再写 converted_text。

转换示例一（中等长度段落，不能追加句子）：
输入段落：{"id":"0-001","text":"韩立一家的日子过得很清苦，父亲每天天不亮就下地干活，母亲在家里纺线织布，一年到头也吃不上几顿带荤腥的饭菜，全家人一直在温饱线上挣扎。"}
正确输出：{"id":"0-001","plan":[{"zh":"清苦","en":"harsh"},{"zh":"干活","en":"toil"},{"zh":"织布","en":"weave"},{"zh":"饭菜","en":"meal"},{"zh":"温饱线","en":"poverty"},{"zh":"挣扎","en":"struggle"}],"converted_text":"韩立一家的日子过得很 harsh（清苦），父亲每天天不亮就下地 toil（干活），母亲在家里 weave（织布），一年到头也吃不上几顿带荤腥的 meal（饭菜），全家人一直在 poverty（温饱线）上 struggle（挣扎）。"}

转换示例二（很短的段落也要替换至少 1 个词）：
输入段落：{"id":"0-002","text":"这位贵客，是跟他血缘很近的一位至亲，他的亲三叔。"}
正确输出：{"id":"0-002","plan":[{"zh":"至亲","en":"relative"}],"converted_text":"这位贵客，是跟他血缘很近的一位 relative（至亲），他的亲三叔。"}

转换示例三（同一句不得堆叠超过 3 项）：
输入段落：{"id":"0-003","text":"他心里十分紧张，脸上却依旧平静，连声音也没有变化。"}
正确输出：{"id":"0-003","plan":[{"zh":"紧张","en":"tense"},{"zh":"平静","en":"calm"},{"zh":"细微","en":"subtle"}],"converted_text":"他心里十分 tense（紧张），脸上却依旧 calm（平静），连声音也没有 subtle（细微）变化。"}
（该句只放 3 项，其余词保持中文。）

JSON 输出完整示例：
{"chapter_id":1,"chapter_title":"第一章","paragraphs":[{"id":"1-001","plan":[{"zh":"掩饰","en":"conceal"},{"zh":"紧张","en":"nervousness"}],"converted_text":"她试图 conceal（掩饰）自己的 nervousness（紧张）。","inserted_terms":[{"word":"conceal","lemma":"conceal","meaning":"掩饰","part_of_speech":"verb","cefr":"B2"},{"word":"nervousness","lemma":"nervousness","meaning":"紧张","part_of_speech":"noun","cefr":"B2"}]}]}
"""


def build_user_prompt(chapter: Chapter, target: list[VocabularyItem], review: list[VocabularyItem], density: dict, *, retry_note: str | None = None) -> str:
    chinese_chars = len(re.findall(r"[\u3400-\u9fff]", "".join(paragraph.text for paragraph in chapter.paragraphs)))
    minimum_items = max(1, math.ceil(chinese_chars * density["min_per_500_chars"] / 500))
    target_items = max(minimum_items, round(chinese_chars * density["target_per_500_chars"] / 500))
    maximum_items = max(target_items, math.floor(chinese_chars * density["max_per_500_chars"] / 500))
    structured_paragraphs = []
    paragraph_counts: list[dict[str, int]] = []
    for paragraph in chapter.paragraphs:
        paragraph_chars = len(re.findall(r"[\u3400-\u9fff]", paragraph.text))
        paragraph_minimum = max(1, math.floor(paragraph_chars * density["min_per_500_chars"] / 500))
        paragraph_target = max(paragraph_minimum, round(paragraph_chars * density["target_per_500_chars"] / 500))
        paragraph_maximum = max(paragraph_target, math.floor(paragraph_chars * density["max_per_500_chars"] / 500))
        if paragraph_chars < 30:
            paragraph_maximum = max(paragraph_maximum, paragraph_target + 1)
        paragraph_counts.append(
            {
                "chars": paragraph_chars,
                "minimum": paragraph_minimum,
                "target": paragraph_target,
                "maximum": paragraph_maximum,
            }
        )
    # The per-paragraph minimums must add up to the block minimum, otherwise the model receives
    # contradictory instructions and under-delivers.
    total_minimum = sum(count["minimum"] for count in paragraph_counts)
    while total_minimum < minimum_items and paragraph_counts:
        candidate = max(
            range(len(paragraph_counts)),
            key=lambda index: paragraph_counts[index]["chars"] if paragraph_counts[index]["minimum"] < paragraph_counts[index]["maximum"] else -1,
        )
        if paragraph_counts[candidate]["minimum"] >= paragraph_counts[candidate]["maximum"]:
            break
        paragraph_counts[candidate]["minimum"] += 1
        total_minimum += 1
    for paragraph, count in zip(chapter.paragraphs, paragraph_counts):
        structured_paragraphs.append(
            {
                "id": paragraph.id,
                "text": paragraph.text,
                "minimum_items": count["minimum"],
                "target_items": max(count["minimum"], count["target"]),
                "maximum_items": max(count["minimum"], count["maximum"]),
            }
        )
    payload = {
        "chapter_id": chapter.chapter_id,
        "chapter_title": chapter.chapter_title,
        "paragraphs": structured_paragraphs,
        "target_vocabulary": [item.model_dump(mode="json") for item in target],
        "review_vocabulary": [item.model_dump(mode="json") for item in review],
        "density": density,
        "chinese_characters": chinese_chars,
        "minimum_learning_items": minimum_items,
        "target_learning_items": target_items,
        "maximum_learning_items": maximum_items,
    }
    prefix = "请严格返回合法 JSON，结构必须与系统消息的 JSON 示例一致。每个段落必须先写 plan 再写 converted_text。必须达到 JSON 中 minimum_learning_items 的总替换次数，并尽量达到 target_learning_items；这是整块总数，不是每段数量。整块中 C1 词汇应占学习项总数的 20% 左右，B1 约 20%。"
    if retry_note:
        prefix += f" 上次输出失败：{retry_note}。请缩短非必要措辞并逐项核对段落 ID。"
    return prefix + "\nJSON 输入：\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
