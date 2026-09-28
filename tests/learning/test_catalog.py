import httpx
import pytest

from app.learning.catalog import CATALOG, fetch_collection


@pytest.mark.parametrize("key,count", [("python", 16), ("numpy", 11), ("ai", 7)])
def test_collections_download_every_page_in_syllabus_order_with_provenance(key, count):
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            text=f"<title>Python 3.14</title><main><h1>{name}</h1><p>Original {name}.</p><h2>Example</h2><pre>x = 1\n\nprint(x)</pre></main>",
            headers={"content-type": "text/html"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        doc = fetch_collection(key, client=client)
        duplicate = fetch_collection(key, client=client)
    assert doc.id == duplicate.id
    assert doc.page_count == count
    assert len(requested) == count * 2
    assert [page.url for page in doc.source_pages] == CATALOG[key]["urls"]
    assert all(section.source_url in CATALOG[key]["urls"] for section in doc.sections)
    assert len({s.id for s in doc.sections}) == len(doc.sections)
    assert doc.sections[-1].blocks[0].text == "x = 1\n\nprint(x)"


def test_collection_failure_does_not_return_a_partial_manual():
    def handler(request):
        if "classes.html" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(
            200,
            text="<title>Python 3.14</title><main><h1>Chapter</h1><p>Original text.</p></main>",
            headers={"content-type": "text/html"},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        fetch_collection("python", client=client)


def test_collection_rejects_mixed_version_snapshots():
    def handler(request):
        version = "3.13" if "classes.html" in str(request.url) else "3.14"
        return httpx.Response(
            200,
            text=f"<title>Python {version}</title><main><h1>Chapter</h1><p>Original text.</p></main>",
            headers={"content-type": "text/html"},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValueError, match="版本"),
    ):
        fetch_collection("python", client=client)
