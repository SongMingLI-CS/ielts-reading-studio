from __future__ import annotations

from ielts_novel.processors.vocabulary_importer import parse_vocabulary_text

SAMPLE = """学科
经济
钱
wage
n. 工资
同
pay; salary
例
Participants were supplied with bicycles.【C4，T3，R1】该公司为参与者提供了自行车。
income
英 ['ɪnkʌm] 美 ['ɪnkʌmə]
n. 收入，所得，收益
记
in（进入）+ come（来）→进来的东西→收入
例
There has also been a renaissance in communal cheese production.【C5，T4，R1】该地区集体奶酪生产活动的复兴。
refund
英 [ˈriːfʌnd] 美 ['rifʌnd]
n. 归还；偿还额；退款
revenue
v. 收入；税收
look after
v. 照顾
in
prep. 在……里面
"""


def test_parses_words_phonetics_meanings_and_examples():
    entries = parse_vocabulary_text(SAMPLE, known_topics={"学科", "经济"})
    by_word = {entry.word: entry for entry in entries}
    assert "wage" in by_word and "income" in by_word
    assert by_word["income"].phonetic == "/ˈɪnkʌm/"
    assert by_word["income"].meaning == "收入，所得，收益"
    assert by_word["wage"].synonyms == ["pay", "salary"]
    assert by_word["wage"].example_sentence.startswith("Participants were supplied")
    assert "该公司" not in by_word["wage"].example_sentence
    assert by_word["look after"].category == "phrasal_verb"
    assert by_word["revenue"].category == "verb"


def test_keeps_topic_and_maps_categories():
    entries = parse_vocabulary_text(SAMPLE, known_topics={"学科", "经济"})
    by_word = {entry.word: entry for entry in entries}
    assert by_word["wage"].topic == "经济"
    assert by_word["income"].category == "noun"
    assert by_word["wage"].category == "noun"


def test_skips_function_words_without_pos_and_trailing_junk():
    entries = parse_vocabulary_text(SAMPLE, known_topics={"学科", "经济"})
    assert all(entry.meaning for entry in entries)
    assert "同" not in {entry.word for entry in entries}
    assert "记" not in {entry.word for entry in entries}


SAMPLE_YU = """MP3-01
Word List 1
词根、词缀预习表
sorb
吸收
absorb v. 吸收；同化
emperor
［ˈempərə(r)］
n. 皇帝；君主
例　The executive of a republic cannot do what a king or an emperor does. 共和国的领导者不能像国王那样行事。
exact
*
［igˈzækt］
a. 精确的；准确的
记　词根记忆：ex（出）＋act（做）→做出精确的结果
例　What is the exact amount left in your savings account? 你储蓄账户上的准确余额是多少？
派　exactly（ad. 正确地；完全地）
traditional
［trəˈdiʃənl］
a. 传统的，惯例的
搭　traditional views 传统观点；traditional belief 传统信条
例　Rosewood is a pure example of a traditional country house. 红木建筑是这个地区传统住宅的代表。
lack
［læk］
n./vt. 缺乏，不足，没有
"""


def test_parses_the_mnemonic_book_layout():
    entries = parse_vocabulary_text(SAMPLE_YU)
    by_word = {entry.word: entry for entry in entries}
    assert {"emperor", "exact", "traditional", "lack"} <= set(by_word)
    assert by_word["emperor"].phonetic == "/ˈempərə(r)/"
    assert by_word["emperor"].meaning == "皇帝；君主"
    assert by_word["exact"].part_of_speech == "adjective"
    assert by_word["exact"].example_sentence.startswith("What is the exact amount")
    assert "精确" not in by_word["exact"].example_sentence
    assert "traditional views" in by_word["traditional"].collocation
    assert by_word["lack"].part_of_speech == "noun"
    assert "absorb" in by_word  # one-line preview table entry


def test_parses_derivative_entries_from_the_pai_block():
    entries = parse_vocabulary_text(SAMPLE_YU)
    by_word = {entry.word: entry for entry in entries}
    assert "exactly" in by_word
    assert by_word["exactly"].part_of_speech == "adverb"
    assert by_word["exactly"].category == "adjective_adverb"


def test_entries_to_catalogue_drops_function_words_and_keeps_metadata():
    from ielts_novel.processors.vocabulary_importer import entries_to_catalogue

    catalogue = {item.lemma: item for item in entries_to_catalogue(parse_vocabulary_text(SAMPLE_YU))}
    assert catalogue["emperor"].phonetic == "/ˈempərə(r)/"
    assert catalogue["traditional"].collocation.startswith("traditional views")
    assert catalogue["emperor"].cefr == "B2"
    assert all(len(item.word) >= 3 for item in catalogue.values())

    entries = parse_vocabulary_text(SAMPLE, known_topics={"学科", "经济"})
    assert all(entry.meaning for entry in entries)
    assert "同" not in {entry.word for entry in entries}
    assert "记" not in {entry.word for entry in entries}
