from __future__ import annotations

import html
import json
import os
from pathlib import Path
from uuid import uuid4

from app.models import QuestionGroup, QuestionType, ReadingPackage

from .json_exporter import _require_validated


def export_html(package: ReadingPackage, path: str | Path) -> Path:
    _require_validated(package)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    markup = _render(package)
    temporary = destination.parent / f"{destination.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(markup)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def _render(package: ReadingPackage) -> str:
    passage = package.passage
    passage_markup = "\n".join(
        f'<p id="paragraph-{_escape(paragraph.label)}"><strong>{_escape(paragraph.label)}</strong> '
        f"{_escape(paragraph.text)}</p>"
        for paragraph in passage.paragraphs
    )
    groups_markup = "\n".join(_render_group(group) for group in package.question_groups)
    answers = {
        str(question.number): {
            "answer": question.answer,
            "acceptable": question.acceptable_answers,
            "evidenceParagraph": question.evidence_paragraph,
            "evidenceQuote": question.evidence_quote,
            "explanation": question.chinese_explanation,
            "distractors": question.distractor_explanations,
        }
        for group in package.question_groups
        for question in group.questions
    }
    vocabulary = [entry.model_dump(mode="json") for entry in passage.vocabulary]
    return rf"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(passage.title)} — IELTS Academic Reading Practice</title>
<style>
:root {{ color-scheme: light; font-family: Georgia, serif; background:#f5f2eb; color:#20242a; }}
body {{ max-width: 980px; margin: 0 auto; padding: 2rem; line-height: 1.65; }}
main {{ background:white; padding:2rem 3rem; box-shadow:0 8px 30px #0001; }}
.meta {{ color:#59636e; font-family:system-ui,sans-serif; }}
.question {{ margin:1rem 0; padding:.8rem; border-left:3px solid #ccd6df; }}
label, select, input, button {{ font-family:system-ui,sans-serif; }}
input[type=text], select {{ min-width:16rem; padding:.45rem; }}
button {{ padding:.65rem 1rem; margin:.5rem .5rem .5rem 0; cursor:pointer; }}
#evidence-panel {{ margin-top:2rem; padding:1rem; background:#eef6f2; }}
.correct {{ border-left-color:#207a4b; }} .incorrect {{ border-left-color:#b33a3a; }}
.vocab {{ border-bottom:1px dotted; cursor:help; }}
@media (max-width:700px) {{ body{{padding:0}} main{{padding:1rem}} }}
</style>
</head>
<body><main>
<header><p class="meta">IELTS Academic Reading Practice · {_escape(passage.difficulty.value.title())}</p>
<h1>{_escape(passage.title)}</h1><p class="meta">Elapsed: <span id="timer">00:00</span></p></header>
<article>{passage_markup}</article>
<form id="practice-form"><h2>Questions</h2>{groups_markup}
<button id="submit-practice" type="submit">Submit answers</button>
<button id="show-analysis" type="button" hidden>查看解析</button></form>
<section id="evidence-panel" hidden><h2>Answers and analysis</h2><p id="score"></p><div id="analysis"></div></section>
<section><h2>Vocabulary</h2><div id="vocabulary"></div></section>
<script type="application/json" id="answer-key">{_safe_json(answers)}</script>
<script type="application/json" id="vocabulary-data">{_safe_json(vocabulary)}</script>
<script>
const started=Date.now(), timer=document.getElementById('timer');
setInterval(()=>{{const s=Math.floor((Date.now()-started)/1000);timer.textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0')}},1000);
const key=JSON.parse(document.getElementById('answer-key').textContent);
const norm=v=>String(v??'').normalize('NFKC').trim().replace(/\s+/g,' ').toLowerCase();
document.getElementById('practice-form').addEventListener('submit',event=>{{
 event.preventDefault(); let correct=0; const rows=[];
 for(const [number,item] of Object.entries(key)){{
  const fields=[...document.querySelectorAll(`[name="q${{number}}"]`)];
  const chosen=fields.find(f=>f.checked)?.value ?? fields[0]?.value ?? '';
  const accepted=[item.answer,...item.acceptable].map(norm); const ok=accepted.includes(norm(chosen));
  if(ok) correct++; const block=document.getElementById(`question-${{number}}`); block.classList.add(ok?'correct':'incorrect');
  rows.push(`<section><h3>${{number}}. ${{ok?'✓':'✗'}} — ${{escapeHtml(item.answer)}}</h3><p><b>Evidence ${{escapeHtml(item.evidenceParagraph)}}:</b> ${{escapeHtml(item.evidenceQuote)}}</p><p>${{escapeHtml(item.explanation)}}</p></section>`);
 }}
 document.getElementById('score').textContent=`Score: ${{correct}} / ${{Object.keys(key).length}}`;
 document.getElementById('analysis').innerHTML=rows.join('');
 document.getElementById('evidence-panel').hidden=false; document.getElementById('show-analysis').hidden=false;
}});
document.getElementById('show-analysis').addEventListener('click',()=>document.getElementById('evidence-panel').scrollIntoView({{behavior:'smooth'}}));
function escapeHtml(value){{const node=document.createElement('div');node.textContent=value??'';return node.innerHTML}}
const vocab=JSON.parse(document.getElementById('vocabulary-data').textContent);
document.getElementById('vocabulary').innerHTML=vocab.map(v=>`<p><span class="vocab" title="${{escapeHtml((v.collocations||[]).join(', '))}}"><b>${{escapeHtml(v.word)}}</b></span> ${{escapeHtml(v.pronunciation||'')}} · ${{escapeHtml(v.part_of_speech||'')}} · ${{escapeHtml(v.chinese_meaning||'')}}<br>${{escapeHtml(v.example||'')}}</p>`).join('');
</script>
</main></body></html>"""


def _render_group(group: QuestionGroup) -> str:
    options = "".join(f"<li>{_escape(option)}</li>" for option in group.options)
    questions = "".join(_render_question(group, question.number, question.prompt) for question in group.questions)
    limit = f" (NO MORE THAN {group.word_limit} WORDS)" if group.word_limit else ""
    return (
        f"<section><h3>{_escape(group.type.value.replace('_', ' ').title())}</h3>"
        f"<p>{_escape(group.instructions)}{_escape(limit)}</p>"
        f"<ol>{options}</ol>{questions}</section>"
    )


def _render_question(group: QuestionGroup, number: int, prompt: str) -> str:
    name = f"q{number}"
    if group.type in {QuestionType.MULTIPLE_CHOICE, QuestionType.MATCHING_HEADINGS, QuestionType.MATCHING_INFORMATION}:
        choices = "".join(
            f'<option value="{_escape(option)}">{_escape(option)}</option>' for option in group.options
        )
        control = f'<select name="{name}"><option value="">Select…</option>{choices}</select>'
    elif group.type in {QuestionType.TRUE_FALSE_NOT_GIVEN, QuestionType.YES_NO_NOT_GIVEN}:
        values = (
            ["TRUE", "FALSE", "NOT GIVEN"]
            if group.type == QuestionType.TRUE_FALSE_NOT_GIVEN
            else ["YES", "NO", "NOT GIVEN"]
        )
        control = " ".join(
            f'<label><input type="radio" name="{name}" value="{value}"> {value}</label>'
            for value in values
        )
    else:
        control = f'<input type="text" name="{name}" autocomplete="off">'
    return f'<div class="question" id="question-{number}"><p><b>{number}.</b> {_escape(prompt)}</p>{control}</div>'


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _safe_json(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
