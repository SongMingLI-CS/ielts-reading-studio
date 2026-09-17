from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from ielts_novel.models import VocabularyItem
from ielts_novel.processors.vocabulary_selector import CATEGORY_RATIOS, CEFR_RATIOS, LOW_VALUE
from ielts_novel.providers.base import ModelProvider

CATEGORY_LABELS = {
    "verb": "雅思高频动词",
    "adjective_adverb": "雅思高频形容词或副词",
    "noun": "学术英语常用名词",
    "collocation": "高频固定搭配（2～4 个单词的短语）",
    "phrasal_verb": "短语动词（动词＋介词或副词）",
}

SYSTEM_PROMPT = """你是雅思词汇教研员，负责编写雅思阅读、写作、口语通用且适合**中文小说情境改写**的高级词汇表。
只返回合法 JSON，不要返回 Markdown 代码块或解释。每条必须包含：
word（词或短语原形）、lemma（小写原形）、meaning（简体中文释义，2～8 个汉字）、
part_of_speech（verb/noun/adjective/adverb/phrase/phrasal verb 之一）、cefr（B1、B2 或 C1）、
phonetic（国际音标，形如 /kənˈsiːl/）、collocation（常见搭配）、example_sentence（不超过 12 个英文词的原创例句）、
category（verb、adjective_adverb、noun、collocation、phrasal_verb 之一）。
禁止收录 woman、man、good、go、bad、nice、thing 等过于基础且无学习价值的词，禁止专有名词、古英语、生僻词。
输出格式：{"items":[{"word":"...","lemma":"...","meaning":"...","part_of_speech":"...","cefr":"B2","phonetic":"/.../","collocation":"...","example_sentence":"...","category":"..."}]}"""

DOMAINS = (
    "情绪与心理（紧张、犹豫、期待、恐惧、隐忍）",
    "动作与姿态（凝视、退缩、疾行、握紧、转身）",
    "环境与景物（山雾、暮色、废墟、潺潺、荒凉）",
    "对话与语气（低语、反问、命令、辩解、沉默）",
    "人物与关系（长辈、同门、陌生人、恩情、疏远）",
    "冲突与转折（冲突、背叛、抉择、突袭、转机）",
    "时间与节奏（片刻、漫长、骤然、渐渐、反复）",
    "抽象品质（毅力、谨慎、野心、善意、执念）",
    "学术研究与论证（假设、证据、推论、质疑、验证）",
    "科技与创新（发明、效率、故障、突破、普及）",
    "自然与生态（气候、物种、污染、栖息地、循环）",
    "城市与交通（拥堵、规划、通勤、枢纽、扩张）",
    "健康与医疗（症状、康复、免疫、疗程、营养）",
    "法律与规则（条款、判决、违规、辩护、约束）",
    "教育与学习（课程、记忆、考试、启发、实践）",
    "商业与职场（谈判、预算、绩效、竞争、协作）",
    "艺术与文化（旋律、意象、风格、传承、审美）",
    "历史与考古（遗迹、年代、考证、兴衰、传说）",
    "饮食与烹饪（滋味、食材、腌制、火候、节庆）",
    "体育与竞技（训练、耐力、裁判、对抗、破纪录）",
    "权力与秩序（统治、服从、叛乱、盟约、制裁）",
    "信仰与迷信（仪式、禁忌、预兆、供奉、敬畏）",
    "逃亡与追捕（追踪、躲藏、突围、埋伏、脱身）",
    "策略与谋划（布局、诱敌、窥探、试探、后手）",
    "日常起居（柴火、井水、缝补、炊烟、温饱）",
    "旅途与迁徙（跋涉、驿站、行囊、漂泊、落脚）",
    "商贩与集市（讨价、货摊、布匹、赊账、铜钱）",
    "农耕与季节（播种、收成、旱涝、荒年、节令）",
    "技艺与手艺（锻造、雕刻、织造、熬制、师承）",
    "命运与抉择（机缘、无常、代价、取舍、宿命）",
    "外貌与气质（清瘦、英气、憨厚、憔悴、凛然）",
    "声音与光线（轰鸣、沙哑、朦胧、斑斓、刺目）",
    "群体与舆论（流言、围观、威望、排斥、附和）",
    "家庭与亲情（抚养、牵挂、裂痕、团聚、责任）",
    "意外与灾祸（崩塌、失火、瘟疫、逃亡、善后）",
    "度量与比较（悬殊、估算、临界、陡增、几乎）",
    "心理挣扎（懊悔、自欺、动摇、隐忍、释然）",
    "科技评测与数据（样本、趋势、偏差、阈值、显著）",
    "文化与习俗差异（习俗、礼仪、忌讳、融入、隔阂）",
    "环境政策（法规、补贴、限额、排放、监督）",
)


@dataclass(frozen=True)
class Bucket:
    cefr: str
    category: str
    target: int

    @property
    def key(self) -> str:
        return f"{self.cefr}:{self.category}"


@dataclass
class BuildReport:
    requested: int = 0
    accepted: int = 0
    rejected: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_seconds: float = 0.0
    buckets: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "requested": self.requested,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "buckets": self.buckets,
            "failures": self.failures,
        }


def build_buckets(total: int) -> list[Bucket]:
    buckets: list[Bucket] = []
    for category, category_ratio in CATEGORY_RATIOS.items():
        for cefr, cefr_ratio in CEFR_RATIOS.items():
            buckets.append(Bucket(cefr=cefr, category=category, target=round(total * category_ratio * cefr_ratio)))
    return buckets



def _prompt(bucket: Bucket, count: int, avoid: list[str], domain: str = "") -> str:
    avoid_note = f"以下词已收录，禁止重复：{', '.join(avoid)}。" if avoid else ""
    domain_note = f"本批词必须围绕「{domain}」这一情境主题，便于嵌入中文小说叙述。" if domain else ""
    return (
        f"请编写 {count} 个{CATEGORY_LABELS[bucket.category]}，难度为 {bucket.cefr}，"
        f"category 字段固定为 {bucket.category}，cefr 字段固定为 {bucket.cefr}。{domain_note}"
        f"这些词必须适合中国读者的雅思备考，中文释义要符合雅思语境。{avoid_note}"
        f'只返回 JSON：{{"items":[{{"word":"...","lemma":"...","meaning":"...","part_of_speech":"...","cefr":"{bucket.cefr}","phonetic":"/.../","collocation":"...","example_sentence":"...","category":"{bucket.category}"}}]}}'
    )


LABEL_SYSTEM_PROMPT = """你是雅思词汇教研员，负责为给定英文词标注 CEFR 等级并给出常见搭配。
cefr 只能是 B1、B2 或 C1：B1 为雅思 4.5-5.5 分常见词，B2 为 6-7 分核心词，C1 为 7.5 分以上学术或书面词。
collocation 用英文给出一个 2～4 词的常见搭配。
只返回 JSON：{"items":[{"word":"...","cefr":"B2","collocation":"..."}]}"""
PHONETIC_SYSTEM_PROMPT = """你是英语语音专家，负责为英文词或短语标注国际音标（英式）。
phonetic 用 /.../ 包裹，例如 /kənˈsiːl/。只返回 JSON：{"items":[{"word":"...","phonetic":"/.../"}]}"""


def fill_phonetics(provider: ModelProvider, items: list[VocabularyItem], *, batch_size: int = 60, concurrency: int = 4, log=print) -> dict[str, int]:
    """Fill missing IPA phonetics; returns usage statistics."""
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "filled": 0}
    pending = [item for item in items if not item.phonetic]
    batches = [pending[index:index + batch_size] for index in range(0, len(pending), batch_size)]
    log(f"待补音标：{len(pending)} 条，共 {len(batches)} 批")
    lock = threading.Lock()

    def label_batch(batch: list[VocabularyItem]):
        listing = "、".join(item.word for item in batch)
        payload, input_tokens, output_tokens = provider.complete_json(
            system=PHONETIC_SYSTEM_PROMPT,
            user=f"请为下面 {len(batch)} 个词标注英式音标：{listing}",
            max_tokens=max(2048, len(batch) * 25),
        )
        return batch, list(payload.get("items", [])), input_tokens, output_tokens

    def run(batch: list[VocabularyItem]):
        try:
            return label_batch(batch)
        except Exception:
            if len(batch) <= 15:
                raise
            midpoint = len(batch) // 2
            parts = [run(batch[:midpoint]), run(batch[midpoint:])]
            return batch, [label for part in parts for label in part[1]], sum(part[2] for part in parts), sum(part[3] for part in parts)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for future in as_completed([pool.submit(run, batch) for batch in batches]):
            try:
                batch, labels, input_tokens, output_tokens = future.result()
            except Exception as exc:  # noqa: BLE001 - a failed batch must not stop the whole fill
                log(f"音标批次失败：{type(exc).__name__}")
                continue
            mapping = {str(label.get("word") or "").strip().lower(): str(label.get("phonetic") or "").strip() for label in labels if isinstance(label, dict)}
            with lock:
                usage["requests"] += 1
                usage["input_tokens"] += input_tokens
                usage["output_tokens"] += output_tokens
                for item in batch:
                    phonetic = mapping.get(item.word.lower()) or mapping.get(item.lemma.lower())
                    if phonetic and phonetic.startswith("/"):
                        item.phonetic = phonetic
                        usage["filled"] += 1
    return usage



def label_cefr_and_collocations(provider: ModelProvider, items: list[VocabularyItem], *, batch_size: int = 60, concurrency: int = 4, log=print) -> dict[str, int]:
    """Label imported vocabulary with CEFR levels and collocations; returns the token usage.

    A batch that comes back as invalid JSON (usually because the response was truncated) is split
    in half and retried, so one oversized response cannot leave a whole block unlabelled.
    """
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "labelled": 0}
    batches = [items[index:index + batch_size] for index in range(0, len(items), batch_size)]
    lock = threading.Lock()

    def label_batch(batch: list[VocabularyItem]):
        listing = "、".join(f"{item.word}={item.meaning}" for item in batch)
        payload, input_tokens, output_tokens = provider.complete_json(
            system=LABEL_SYSTEM_PROMPT,
            user=f"请为下面 {len(batch)} 个词标注 CEFR 与搭配：{listing}",
            max_tokens=max(2048, len(batch) * 45),
        )
        return batch, list(payload.get("items", [])), input_tokens, output_tokens

    def run(batch: list[VocabularyItem]):
        try:
            return label_batch(batch)
        except Exception:
            if len(batch) <= 15:
                raise
            midpoint = len(batch) // 2
            parts = [run(batch[:midpoint]), run(batch[midpoint:])]
            return batch, [label for part in parts for label in part[1]], sum(part[2] for part in parts), sum(part[3] for part in parts)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for future in as_completed([pool.submit(run, batch) for batch in batches]):
            try:
                batch, labels, input_tokens, output_tokens = future.result()
            except Exception as exc:  # noqa: BLE001 - keep the imported data usable
                log(f"CEFR 标注批次失败：{type(exc).__name__}")
                continue
            mapping = {str(label.get("word") or "").strip().lower(): label for label in labels if isinstance(label, dict)}
            with lock:
                usage["requests"] += 1
                usage["input_tokens"] += input_tokens
                usage["output_tokens"] += output_tokens
                for item in batch:
                    label = mapping.get(item.word.lower()) or mapping.get(item.lemma.lower())
                    if not label:
                        continue
                    level = str(label.get("cefr") or "").strip().upper()
                    if level in {"B1", "B2", "C1"}:
                        item.cefr = level
                    collocation = str(label.get("collocation") or "").strip()
                    if collocation:
                        item.collocation = collocation
                    usage["labelled"] += 1
    return usage


def deficit_buckets(target_total: int, current: dict[str, int]) -> list[Bucket]:
    """Buckets needed so the merged catalogue reaches the requested CEFR and category mix."""
    buckets: list[Bucket] = []
    for category, category_ratio in CATEGORY_RATIOS.items():
        for cefr, cefr_ratio in CEFR_RATIOS.items():
            wanted = round(target_total * category_ratio * cefr_ratio)
            have = current.get(f"{cefr}:{category}", 0)
            if wanted > have:
                buckets.append(Bucket(cefr=cefr, category=category, target=wanted - have))
    return buckets


def count_buckets(items: list[VocabularyItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[f"{item.cefr}:{item.category}"] = counts.get(f"{item.cefr}:{item.category}", 0) + 1
    return counts

def _valid(items: list, bucket: Bucket, seen: set[str]) -> tuple[list[VocabularyItem], int]:
    accepted: list[VocabularyItem] = []
    rejected = 0
    for raw in items:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        word = str(raw.get("word") or "").strip()
        lemma = str(raw.get("lemma") or word).strip().lower()
        meaning = str(raw.get("meaning") or "").strip()
        if len(word) < 3 or lemma in seen or lemma in LOW_VALUE or not meaning:
            rejected += 1
            continue
        if not any("\u3400" <= char <= "\u9fff" for char in meaning):
            rejected += 1
            continue
        item = VocabularyItem.model_validate({**raw, "word": word, "lemma": lemma, "cefr": bucket.cefr, "category": bucket.category})
        accepted.append(item)
        seen.add(lemma)
    return accepted, rejected



class VocabularyBuilder:
    """Builds the 5000-7000 item IELTS catalogue in resumable, quota-controlled batches."""

    def __init__(
        self,
        provider: ModelProvider,
        path: str | Path,
        *,
        target: int = 6000,
        batch_size: int = 50,
        concurrency: int = 4,
        log=print,
        seed: list[VocabularyItem] | None = None,
        buckets: list[Bucket] | None = None,
    ):
        self.provider = provider
        self.path = Path(path)
        self.partial_path = self.path.with_name(self.path.stem + "_partial.json")
        self.target = target
        self.batch_size = batch_size
        self.concurrency = max(1, concurrency)
        self.log = log
        self.seed = list(seed or [])
        self.buckets = buckets
        self._lock = threading.Lock()

    def _load(self) -> tuple[dict[str, VocabularyItem], set[str], dict[str, int]]:
        items: dict[str, VocabularyItem] = {item.lemma.lower(): item for item in self.seed}
        done: set[str] = set()
        attempts: dict[str, int] = {}
        if self.partial_path.exists():
            saved = json.loads(self.partial_path.read_text(encoding="utf-8"))
            for payload in saved.get("items", []):
                item = VocabularyItem.model_validate(payload)
                items.setdefault(item.lemma.lower(), item)
            done = set(saved.get("done_batches", []))
            attempts = {str(key): int(value) for key, value in (saved.get("attempts") or {}).items()}
        return self._merge_existing_metadata(items), done, attempts

    def _merge_existing_metadata(self, items: dict[str, VocabularyItem]) -> dict[str, VocabularyItem]:
        """Keep phonetics/collocations/examples that were filled into an earlier catalogue file.

        Re-running the builder must never downgrade the published catalogue (an earlier phonetics
        fill, for example), so richer fields from the existing file win over empty ones.
        """
        if not self.path.exists():
            return items
        try:
            existing = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return items
        for payload in existing:
            item = VocabularyItem.model_validate(payload)
            current = items.get(item.lemma.lower())
            if current is None:
                items[item.lemma.lower()] = item
                continue
            items[item.lemma.lower()] = current.model_copy(
                update={
                    "phonetic": current.phonetic or item.phonetic,
                    "collocation": current.collocation or item.collocation,
                    "example_sentence": current.example_sentence or item.example_sentence,
                }
            )
        return items

    def _save(self, items: dict[str, VocabularyItem], done: set[str], attempts: dict[str, int]) -> None:
        self.partial_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.partial_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"items": [item.model_dump(mode="json") for item in items.values()], "done_batches": sorted(done), "attempts": dict(sorted(attempts.items()))},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.partial_path)

    def build(self, *, rounds: int = 8) -> BuildReport:
        """Generate until the catalogue reaches the target, re-computing deficits after each round."""
        report = BuildReport(requested=self.target)
        started = time.monotonic()
        items, done, attempts = self._load()
        for round_index in range(1, rounds + 1):
            if round_index > 1 and len(items) >= self.target:
                break
            buckets = self.buckets if (self.buckets is not None and round_index == 1) else deficit_buckets(self.target, count_buckets(list(items.values())))
            if not buckets:
                break
            self._run_round(round_index, buckets, items, done, attempts, report)
        ordered = sorted(items.values(), key=lambda item: (item.cefr, item.category, item.lemma))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([item.model_dump(mode="json") for item in ordered], ensure_ascii=False, indent=1), encoding="utf-8")
        report.accepted = len(ordered)
        report.elapsed_seconds = time.monotonic() - started
        return report

    def _run_round(self, round_index: int, buckets: list[Bucket], items: dict[str, VocabularyItem], done: set[str], attempts: dict[str, int], report: BuildReport) -> None:
        jobs: list[tuple[Bucket, int, int]] = []
        for bucket in buckets:
            # Attempts are counted per bucket and bounded, so re-running the same command never pays
            # twice for the same under-delivering batch, while a higher target or a wider domain set
            # (the salt) legitimately opens fresh attempts.
            salt = f"d{len(DOMAINS)}"
            wanted_total = round(self.target * CATEGORY_RATIOS[bucket.category] * CEFR_RATIOS[bucket.cefr])
            cap = max(2, -(-wanted_total // self.batch_size) + 1)
            used = attempts.get(bucket.key, 0)
            if used >= cap:
                continue
            batches = max(1, -(-bucket.target // self.batch_size))
            for index in range(batches):
                attempt_index = used + index
                if attempt_index >= cap:
                    break
                key = f"{bucket.key}#{attempt_index}#{salt}"
                if key in done:
                    continue
                jobs.append((bucket, attempt_index, min(self.batch_size, bucket.target - index * self.batch_size) or self.batch_size))
        self.log(f"第 {round_index} 轮：目标 {self.target} 条，已有 {len(items)} 条，待生成 {len(jobs)} 批")
        if not jobs:
            return
        recent: list[str] = []

        def generate(bucket: Bucket, index: int, size: int):
            with self._lock:
                ordered = sorted(items)
                start = (index * 150) % max(1, len(ordered))
                avoid = recent[-120:] + (ordered[start:] + ordered[:start])[:60]
            domain = DOMAINS[index % len(DOMAINS)]
            payload, input_tokens, output_tokens = self.provider.complete_json(
                system=SYSTEM_PROMPT,
                user=_prompt(bucket, size, avoid, domain),
                max_tokens=min(8000, max(3000, size * 120)),
            )
            return list(payload.get("items", [])), input_tokens, output_tokens

        def run(job: tuple[Bucket, int, int]):
            bucket, index, size = job
            try:
                items_out, input_tokens, output_tokens = generate(bucket, index, size)
            except Exception:
                if size <= 15:
                    raise
                midpoint = size // 2
                first = generate(bucket, index, midpoint)
                second = generate(bucket, index + len(DOMAINS), size - midpoint)
                items_out = first[0] + second[0]
                input_tokens = first[1] + second[1]
                output_tokens = first[2] + second[2]
            return bucket, index, items_out, input_tokens, output_tokens

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(run, job): job for job in jobs}
            for future in as_completed(futures):
                bucket, index, _size = futures[future]
                key = f"{bucket.key}#r{round_index}#{index}"
                try:
                    bucket, index, raw_items, input_tokens, output_tokens = future.result()
                except Exception as exc:  # noqa: BLE001 - one failed batch must not stop the build
                    report.failures.append(f"{key}: {type(exc).__name__}")
                    self.log(f"批次 {key} 失败：{type(exc).__name__}")
                    continue
                with self._lock:
                    accepted, rejected = _valid(raw_items, bucket, {item.lemma.lower() for item in items.values()})
                    for item in accepted:
                        items.setdefault(item.lemma.lower(), item)
                        recent.append(item.lemma.lower())
                    done.add(key)
                    report.requests += 1
                    report.input_tokens += input_tokens
                    report.output_tokens += output_tokens
                    report.rejected += rejected
                    report.buckets[bucket.key] = report.buckets.get(bucket.key, 0) + len(accepted)
                    attempts[bucket.key] = max(attempts.get(bucket.key, 0), index + 1)
                    self._save(items, done, attempts)
                    self.log(f"批次 {key} 收录 {len(accepted)} 条，累计 {len(items)} 条")
