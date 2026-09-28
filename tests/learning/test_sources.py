import httpx
import pytest

from app.learning.sources import (
    fetch_official_document,
    parse_html,
    parse_markdown,
    validate_official_url,
)


def test_original_code_definitions_table_and_version_are_preserved():
    doc = parse_html(
        """<title>Arrays — NumPy v2.5 Manual</title><main><h1>Arrays</h1>
    <p>An ndarray is a multidimensional array.</p><h2>Creation</h2>
    <pre>import numpy as np\n\na = np.array([1, 2])\nprint(a.shape)</pre>
    <dl><dt>dtype : data-type, optional</dt><dd>The desired data-type.</dd></dl>
    <table><tr><th>Parameter</th><th>Meaning</th></tr><tr><td>shape</td><td>Dimensions</td></tr></table>
    <script>steal()</script></main>""",
        "https://numpy.org/doc/stable/arrays.html",
    )
    assert doc.version == "2.5"
    assert doc.topic == "numpy"
    assert (
        doc.sections[1].blocks[0].text
        == "import numpy as np\n\na = np.array([1, 2])\nprint(a.shape)"
    )
    assert "dtype : data-type, optional" in doc.sections[1].text
    assert doc.sections[1].blocks[-1].rows[1] == ["shape", "Dimensions"]
    assert "steal()" not in doc.model_dump_json()


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.python.org/3/",
        "https://127.0.0.1/",
        "https://numpy.org.evil.test/",
        "https://u:p@numpy.org/doc/",
        "https://numpy.org:8768/doc/",
        "https://numpy.org/admin",
    ],
)
def test_only_official_document_locations_can_be_fetched(url):
    with pytest.raises(ValueError):
        validate_official_url(url)


def test_redirect_cannot_escape_to_local_or_unknown_hosts():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValueError),
    ):
        fetch_official_document("https://numpy.org/doc/stable/", client=client)


def test_markdown_snapshot_is_exact_and_does_not_rewrite_code():
    source = "# Arrays\n\nOriginal words.\n\n## Example\n\n```python\nx = [1, 2]\n\nprint(x)\n```\n"
    doc = parse_markdown(
        source, title="Arrays", topic="python", version="3.13", source_url=""
    )
    assert not doc.official
    assert doc.version == "3.13"
    assert doc.sections[1].blocks[0].text == "x = [1, 2]\n\nprint(x)"
    assert (
        doc.id
        == parse_markdown(
            source, title="Arrays", topic="python", version="3.13", source_url=""
        ).id
    )


def test_empty_or_non_document_html_is_rejected():
    with pytest.raises(ValueError):
        parse_html(
            "<title>Please log in</title><body>No document</body>",
            "https://numpy.org/doc/stable/",
        )


def test_code_inside_parameter_descriptions_keeps_formatting():
    doc = parse_html(
        "<title>Python 3.14</title><main><h1>Function</h1><dl><dt>call(<span>x</span>, <span>y=None</span>)</dt><dd><p>Example:</p><pre>if x:\n    return y</pre></dd></dl></main>",
        "https://docs.python.org/3/library/test.html",
    )
    assert doc.sections[0].blocks[0].text == "call(x, y=None)"
    assert any(
        b.kind == "code" and b.text == "if x:\n    return y"
        for b in doc.sections[0].blocks
    )


def test_nested_lists_and_code_keep_surrounding_original_text():
    doc = parse_html(
        "<title>Python 3.14</title><main><h1>Example</h1><ul><li>Before<pre>  x = 1\n\n</pre>After<ul><li>Nested detail</li></ul></li></ul></main>",
        "https://docs.python.org/3/example.html",
    )
    text = doc.sections[0].text
    assert all(value in text for value in ["Before", "After", "Nested detail"])
    assert (
        next(b.text for b in doc.sections[0].blocks if b.kind == "code")
        == "  x = 1\n\n"
    )


def test_official_meta_redirect_is_validated_and_resolves_version():
    def handler(request):
        text = (
            '<meta http-equiv="refresh" content="0; url=../2.14/index.html">'
            if "/stable/" in str(request.url)
            else "<title>PyTorch documentation</title><main><h1>Tensor</h1><p>A tensor.</p></main>"
        )
        return httpx.Response(200, text=text, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        doc = fetch_official_document(
            "https://docs.pytorch.org/docs/stable/index.html", client=client
        )
    assert doc.version == "2.14"
