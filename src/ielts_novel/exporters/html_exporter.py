from __future__ import annotations

import html
import re
from pathlib import Path

from ielts_novel.models import ConvertedChapter
from ielts_novel.storage.atomic import atomic_write_text


def export_chapter_html(chapter: ConvertedChapter, path: str | Path, *, cumulative: dict[str, int] | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    paragraphs = []
    for paragraph in chapter.paragraphs:
        rendered = html.escape(paragraph.converted_text)
        for term in sorted(paragraph.inserted_terms, key=lambda value: len(value.word), reverse=True):
            attrs = {
                "phonetic": term.phonetic,
                "pos": term.part_of_speech,
                "meaning": term.meaning,
                "collocation": term.collocation,
                "count": str((cumulative or {}).get(term.lemma, 1)),
            }
            span = '<span class="vocab" tabindex="0" ' + " ".join(f'data-{key}="{html.escape(value, quote=True)}"' for key, value in attrs.items()) + f'><strong>{html.escape(term.word)}</strong></span>'
            annotated = rf"\b{re.escape(html.escape(term.word))}\b（{re.escape(html.escape(term.meaning))}）"
            replacement = span + f'<span class="meaning">（{html.escape(term.meaning)}）</span>'
            rendered, replaced = re.subn(annotated, lambda _: replacement, rendered, count=1)
            if not replaced:
                rendered = re.sub(rf"\b{re.escape(html.escape(term.word))}\b", lambda _: span, rendered, count=1)
        paragraphs.append(f'<p data-id="{html.escape(paragraph.id)}">{rendered}</p>')
    content = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(chapter.chapter_title)}</title><style>body{{max-width:48rem;margin:auto;padding:2rem;font:18px/1.9 'Microsoft YaHei',sans-serif;color:#222}}.vocab,.meaning{{background:#fce4ec;padding:0 .15em}}.vocab{{border-radius:.2em;cursor:pointer}}.hide-meanings .meaning{{display:none}}#tip{{position:fixed;display:none;max-width:24rem;background:#fff;border:1px solid #ddd;padding:1rem;box-shadow:0 4px 20px #0002}}</style></head><body><button id="toggle-meanings">隐藏或显示中文释义</button><h1>{html.escape(chapter.chapter_title)}</h1>{''.join(paragraphs)}<div id="tip"></div><script>document.getElementById('toggle-meanings').onclick=()=>document.body.classList.toggle('hide-meanings');document.querySelectorAll('.vocab').forEach(x=>x.onclick=()=>{{let t=document.getElementById('tip');t.textContent=[x.dataset.phonetic,x.dataset.pos,x.dataset.meaning,x.dataset.collocation,'出现 '+x.dataset.count+' 次'].filter(Boolean).join(' · ');t.style.display='block'}});</script></body></html>"""
    atomic_write_text(target, content)
    return target


def export_index_html(entries: list[tuple[int, str]], path: str | Path) -> Path:
    """Render the index page from (chapter_id, title) pairs.

    Keeping the input to a light-weight list avoids re-reading every converted chapter JSON on each
    commit, which used to make the export quadratic as the novel grew into the thousands.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    links = "".join(f'<li><a href="html/第{chapter_id:04d}章.html">{html.escape(title)}</a></li>' for chapter_id, title in entries)
    content = f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>雅思词汇情境阅读目录</title><style>body{{max-width:48rem;margin:3rem auto;font:18px/1.8 "Microsoft YaHei",sans-serif}}a{{color:#8b3058}}</style></head><body><h1>雅思词汇情境阅读目录</h1><ol>{links}</ol></body></html>'
    atomic_write_text(target, content)
    return target
