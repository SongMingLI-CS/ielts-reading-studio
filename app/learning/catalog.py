"""Explicit, ordered reading collections, never an unbounded site crawler."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import httpx

from .models import StudyDocument, StudySourcePage
from .sources import fetch_official_document

CATALOG = {
    "python": {
        "topic": "python",
        "code": "01 / PYTHON",
        "title": "Python 官方教程",
        "description": "完整 16 章：从解释器与语法，到函数、类、标准库和虚拟环境。",
        "url": "https://docs.python.org/3/tutorial/index.html",
        "urls": [
            f"https://docs.python.org/3/tutorial/{name}.html"
            for name in (
                "appetite",
                "interpreter",
                "introduction",
                "controlflow",
                "datastructures",
                "modules",
                "inputoutput",
                "errors",
                "classes",
                "stdlib",
                "stdlib2",
                "venv",
                "whatnow",
                "interactive",
                "floatingpoint",
                "appendix",
            )
        ],
    },
    "numpy": {
        "topic": "numpy",
        "code": "02 / NUMPY",
        "title": "NumPy 入门与基础",
        "description": "11 篇官方文档：入门、快速上手与完整基础主题，覆盖索引、广播和视图。",
        "url": "https://numpy.org/doc/stable/user/basics.html",
        "urls": [
            f"https://numpy.org/doc/stable/user/{name}.html"
            for name in (
                "absolute_beginners",
                "quickstart",
                "basics.creation",
                "basics.indexing",
                "basics.io",
                "basics.types",
                "basics.broadcasting",
                "basics.copies",
                "basics.strings",
                "basics.rec",
                "basics.ufuncs",
            )
        ],
    },
    "ai": {
        "topic": "ai",
        "code": "03 / MACHINE LEARNING",
        "title": "机器学习核心指南",
        "description": "7 篇 scikit-learn 指南：模型、预处理、Pipeline、交叉验证、评估与常见陷阱。",
        "url": "https://scikit-learn.org/stable/user_guide.html",
        "urls": [
            f"https://scikit-learn.org/stable/{name}.html"
            for name in (
                "getting_started",
                "modules/linear_model",
                "modules/preprocessing",
                "modules/compose",
                "modules/cross_validation",
                "modules/model_evaluation",
                "common_pitfalls",
            )
        ],
    },
}


def fetch_collection(key: str, *, client: httpx.Client | None = None) -> StudyDocument:
    if key not in CATALOG:
        raise ValueError("学习路径不存在。")
    if client is None:
        with httpx.Client(
            timeout=httpx.Timeout(20, connect=8), trust_env=False
        ) as owned:
            return fetch_collection(key, client=owned)
    entry = CATALOG[key]
    # httpx clients can be shared between threads. map preserves syllabus order;
    # nothing is saved if even one chapter fails to download or parse.
    with ThreadPoolExecutor(max_workers=3) as pool:
        pages = list(
            pool.map(
                lambda url: fetch_official_document(url, client=client), entry["urls"]
            )
        )
    versions = {page.version for page in pages}
    if len(versions) != 1:
        raise ValueError("官方文档版本在导入期间发生变化，请重试以获取一致的版本。")
    if sum(page.content_characters for page in pages) > 20_000_000:
        raise ValueError("学习路径内容过大，请按单个章节导入。")
    digest = sha256("\n".join(page.content_hash for page in pages).encode()).hexdigest()
    identity = sha256(f"collection-v1\n{key}\n{digest}".encode()).hexdigest()[:24]
    return StudyDocument(
        id=identity,
        title=entry["title"],
        topic=entry["topic"],
        version=pages[0].version,
        source_url=entry["url"],
        official=True,
        content_hash=digest,
        source_pages=[
            StudySourcePage(title=p.title, url=p.source_url, version=p.version)
            for p in pages
        ],
        sections=[
            section.model_copy(
                update={
                    "id": f"p{index}-{section.id}",
                    "source_url": page.source_url,
                    "chapter_title": page.title,
                }
            )
            for index, page in enumerate(pages, 1)
            for section in page.sections
        ],
    )
