from bs4 import BeautifulSoup

from app.learning.annotations import ReadingAnnotations
from app.web.templating import markdown_block


def test_annotations_preserve_exact_original_text_and_do_not_match_substrings_or_api_names():
    text = "The review is arbitrary. np.array is not a word. A homogeneous array contains integers."
    annotations = ReadingAnnotations()
    soup = BeautifulSoup(str(annotations.plain(text)), "html.parser")
    assert soup.get_text() == text
    words = [button.text for button in soup.select("[data-reading-word]")]
    assert "view" not in words
    assert words.count("array") == 1
    assert "arbitrary" in words and "homogeneous" in words
    assert (
        soup.select_one('[data-reading-word="arbitrary"]')["data-chinese"]
        == "任意的；未作特定限制的"
    )


def test_each_word_family_is_annotated_only_once_per_section():
    annotations = ReadingAnnotations()
    first = BeautifulSoup(
        str(annotations.plain("An array contains integers.")), "html.parser"
    )
    second = BeautifulSoup(
        str(annotations.plain("Arrays may contain another array.")), "html.parser"
    )
    assert first.select_one('[data-reading-word="array"]')
    assert second.select('[data-reading-word="Arrays"]') == []
    assert second.get_text() == "Arrays may contain another array."


def test_markdown_formatting_code_and_links_remain_intact():
    text = "**An array** has `dtype` and [array documentation](https://numpy.org/doc/stable/).\n\n```python\narray = 'mutable'\n```"
    result = BeautifulSoup(
        str(ReadingAnnotations().markdown(markdown_block(text))), "html.parser"
    )
    assert result.select_one("strong [data-reading-word]").text == "array"
    assert not result.select("code [data-reading-word], a [data-reading-word]")
    assert result.select_one("pre code").text == "array = 'mutable'\n"
    assert result.select_one("a")["href"] == "https://numpy.org/doc/stable/"


def test_contextual_meanings_override_defaults_without_injecting_html():
    malicious = '"><script>alert(1)</script>'
    annotations = ReadingAnnotations(
        [
            {
                "term": "view",
                "chinese": malicious,
                "usage_note": "Shared data.",
                "source_quote": "A view shares data.",
            }
        ]
    )
    soup = BeautifulSoup(str(annotations.plain("A view shares data.")), "html.parser")
    button = soup.select_one('[data-reading-word="view"]')
    assert button["data-chinese"] == malicious
    assert button["data-meaning-source"] == "本节语境释义"
    assert soup.find("script") is None
    assert soup.get_text() == "A view shares data."


def test_phrases_across_source_line_breaks_preserve_their_whitespace():
    text = "A data\ntype is required."
    result = BeautifulSoup(str(ReadingAnnotations().plain(text)), "html.parser")
    assert result.get_text() == text
    assert result.find(attrs={"data-reading-word": "data\ntype"}) is not None
