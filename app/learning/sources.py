from __future__ import annotations

import re
from hashlib import sha256
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString

from .models import SourceBlock, StudyDocument, StudySection, Topic

MAX_SOURCE_BYTES = 2 * 1024 * 1024
OFFICIAL_HOSTS = {
    "docs.python.org": ("python", ("/",)),
    "numpy.org": ("numpy", ("/doc/",)),
    "scikit-learn.org": (
        "ai",
        (
            "/stable/",
            "/dev/",
            "/1.",
        ),
    ),
    "docs.pytorch.org": ("ai", ("/docs/", "/tutorials/")),
    "pytorch.org": ("ai", ("/docs/", "/tutorials/")),
}


def validate_official_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    entry = OFFICIAL_HOSTS.get(parsed.hostname or "")
    if (
        parsed.scheme != "https"
        or not entry
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
    ):
        raise ValueError(
            "请使用 Python、NumPy、scikit-learn 或 PyTorch 的官方 HTTPS 文档地址。"
        )
    if not any(parsed.path.startswith(prefix) for prefix in entry[1]) or any(
        ord(c) < 32 for c in url
    ):
        raise ValueError("这个地址不是支持的官方文档路径。")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


def fetch_official_document(
    url: str, *, client: httpx.Client | None = None
) -> StudyDocument:
    if client is None:
        with httpx.Client(
            timeout=httpx.Timeout(20, connect=8),
            follow_redirects=False,
            trust_env=False,
        ) as owned:
            return fetch_official_document(url, client=owned)
    current = validate_official_url(url)
    for _ in range(5):
        with client.stream(
            "GET",
            current,
            headers={"User-Agent": "LearningStudio/1.0 (document reader)"},
            follow_redirects=False,
        ) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                current = validate_official_url(
                    urljoin(current, response.headers.get("location", ""))
                )
                continue
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "").lower():
                raise ValueError("该地址没有返回 HTML 文档；Markdown 请使用文件导入。")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    raise ValueError("文档超过 2 MB，请选择更具体的章节。")
                chunks.append(chunk)
            html = b"".join(chunks).decode("utf-8")
            refresh = BeautifulSoup(html, "html.parser").find(
                "meta", attrs={"http-equiv": re.compile("^refresh$", re.IGNORECASE)}
            )
            if refresh:
                target = re.search(
                    r"url\s*=\s*(.+)$", str(refresh.get("content", "")), re.IGNORECASE
                )
                if target:
                    current = validate_official_url(
                        urljoin(current, target[1].strip().strip("\"'"))
                    )
                    continue
            return parse_html(html, current)
    raise ValueError("文档跳转次数过多，请使用最终文档地址。")


def _snapshot(
    blocks: list[SourceBlock],
    *,
    title: str,
    topic: Topic,
    version: str,
    source_url: str,
    official: bool,
    raw: str,
) -> StudyDocument:
    sections: list[StudySection] = []
    section_title = title
    current: list[SourceBlock] = []
    for block in blocks:
        if block.kind == "heading" and block.level <= 3:
            if current:
                sections.append(
                    StudySection(
                        id=f"s{len(sections) + 1}", title=section_title, blocks=current
                    )
                )
                current = []
            section_title = block.text
        else:
            current.append(block)
    if current:
        sections.append(
            StudySection(
                id=f"s{len(sections) + 1}", title=section_title, blocks=current
            )
        )
    if not sections:
        raise ValueError("文档没有可阅读的正文。")
    digest = sha256(raw.encode()).hexdigest()
    identity = sha256(
        f"{source_url}\n{title}\n{topic}\n{version}\n{digest}".encode()
    ).hexdigest()[:24]
    return StudyDocument(
        id=identity,
        title=title,
        topic=topic,
        version=version,
        source_url=source_url,
        official=official,
        content_hash=digest,
        sections=sections,
    )


def parse_html(html: str, url: str) -> StudyDocument:
    url = validate_official_url(url)
    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    main = soup.select_one('main, [role="main"], article')
    if main is None:
        raise ValueError("未找到文档正文，请打开具体文档页面，或使用 Markdown 导入。")
    for node in main.select(
        "script, style, nav, button, .headerlink, .copybtn, .sphinxsidebar, .toctree-wrapper"
    ):
        node.decompose()
    title_node = main.find("h1")
    title = (
        title_node.get_text(" ", strip=True)
        if title_node
        else page_title.split("—")[0].strip()
    )
    topic = OFFICIAL_HOSTS[urlsplit(url).hostname][0]
    match = re.search(
        r"(?:Python\s+|NumPy\s+v?|scikit-learn\s+|PyTorch\s+)(\d+\.\d+(?:\.\d+)?)",
        page_title,
        re.IGNORECASE,
    )
    path_version = re.search(
        r"/(?:docs|tutorials)/(\d+\.\d+(?:\.\d+)?)(?:/|$)", urlsplit(url).path
    )
    version = (
        match[1]
        if match
        else path_version[1]
        if path_version
        else "未标注（以来源页面为准）"
    )
    blocks: list[SourceBlock] = []
    tags = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "li", "dt", "dd", "table"]

    def walk(node):
        if node.name == "pre":
            blocks.append(
                SourceBlock(
                    kind="code", text=node.get_text("", strip=False), language="python"
                )
            )
        elif node.name == "table":
            rows = [
                [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
                for row in node.find_all("tr")
            ]
            blocks.append(SourceBlock(kind="table", rows=[row for row in rows if row]))
        elif node.name.startswith("h") and node.name[1:].isdigit():
            blocks.append(
                SourceBlock(
                    kind="heading",
                    text=node.get_text(" ", strip=True),
                    level=int(node.name[1]),
                )
            )
        elif node.name == "dt":
            blocks.append(
                SourceBlock(
                    kind="parameter", text=node.get_text("", strip=False).strip()
                )
            )
        elif node.name in ("p", "li", "dd") and not node.find(tags):
            text = node.get_text(" ", strip=True)
            if text:
                blocks.append(
                    SourceBlock(kind="list" if node.name == "li" else "text", text=text)
                )
        else:
            inline = []

            def flush_inline():
                text = "".join(inline).strip()
                if text:
                    blocks.append(
                        SourceBlock(
                            kind="list" if node.name == "li" else "text", text=text
                        )
                    )
                inline.clear()

            for child in node.children:
                if isinstance(child, Comment):
                    continue
                if isinstance(child, NavigableString):
                    inline.append(str(child))
                elif child.name in (
                    "span",
                    "a",
                    "code",
                    "em",
                    "strong",
                    "b",
                    "i",
                    "sub",
                    "sup",
                ) and not child.find(tags):
                    inline.append(child.get_text("", strip=False))
                else:
                    flush_inline()
                    walk(child)
            flush_inline()

    walk(main)
    return _snapshot(
        blocks,
        title=title or "官方文档",
        topic=topic,
        version=version,
        source_url=url,
        official=True,
        raw=html,
    )


def parse_markdown(
    markdown: str, *, title: str, topic: Topic, version: str, source_url: str
) -> StudyDocument:
    blocks: list[SourceBlock] = []
    paragraph: list[str] = []
    code: list[str] = []
    fence = ""
    language = ""

    def flush():
        if paragraph:
            blocks.append(SourceBlock(kind="text", text="\n".join(paragraph)))
            paragraph.clear()

    for line in markdown.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2].strip()
            ):
                blocks.append(
                    SourceBlock(kind="code", text="\n".join(code), language=language)
                )
                code.clear()
                fence = ""
            else:
                code.append(line)
            continue
        if marker:
            flush()
            fence, language = marker[1], marker[2].strip()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", line)
        if heading:
            flush()
            blocks.append(
                SourceBlock(kind="heading", text=heading[2], level=len(heading[1]))
            )
        elif not line.strip():
            flush()
        else:
            paragraph.append(line)
    if fence:
        blocks.append(SourceBlock(kind="code", text="\n".join(code), language=language))
    flush()
    return _snapshot(
        blocks,
        title=title,
        topic=topic,
        version=version,
        source_url=source_url,
        official=False,
        raw=markdown,
    )
