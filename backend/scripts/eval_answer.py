"""答案质量评测（端到端）。

检索评测的痛点：自建 gold set 只有 35 条，对配置改动敏感到 ±6%，
hit@1 差 14% 只等于 5 条样本 —— 根本不能当回归门禁。

答案质量评测用带标准答案的题库（TCMQA 33872 题 / LingLan 专家基准），
可以抽几百题做统计，灵敏度和可信度都高得多。

判分方式：把标准答案的文本（选项内容）去标点后做子串匹配，
再退化为 2-gram 覆盖率 ≥0.6 的宽松匹配，抵消「答对了但换说法」。

用法：
    python -m scripts.eval_answer                    # 默认抽 60 题
    python -m scripts.eval_answer --limit 200
    python -m scripts.eval_answer --source tcmqa --seed 7
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pyarrow.parquet as pq  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.rag.extract.entities import EntityExtractor  # noqa: E402
from app.rag.extract.tokenize import init_jieba  # noqa: E402
from app.rag.retrieve import pipeline  # noqa: E402

EXT = ROOT / "kb_src" / "external"
PUNCT = re.compile(r"[\s，。、；：？！“”‘’（）()《》〈〉【】\[\]{}·,.;:?!\"'/\\|+*=<>~`@#$%^&_\-]+")

# TCMQA 里混了大量西药分析/药理/法规管理题（"量化分子量测定"→MS、"药品不良反应"、
# "化学结构"…），跟本项目的中医知识没关系。
# 黑名单漏得太多，改用**白名单**：只保留 topic 明确属中医方向的。
ON_TOPIC = re.compile(
    r"中医|中药|方剂|方药|针灸|温病|伤寒|金匮|经络|脉诊|脏腑|辨证|论治|"
    r"本草|四气五味|性味归经|炮制|腧穴|望诊|闻诊|问诊|切诊|"
    r"解表|清热|泻下|补虚|温里|理气|消食|止血|活血|化痰|安神|开窍|"
    r"祛湿|利水|收涩|驱虫|涌吐|攻下|和解|表里|气血津液|经络腧穴|"
    r"妇科|儿科|内科|外科|伤科|五官|皮肤科"
)


def topic_ok(topic: str) -> bool:
    t = (topic or "").strip()
    if not t:
        return False
    return bool(ON_TOPIC.search(t))


def normalize(s: str) -> str:
    return PUNCT.sub("", s or "")


def bigrams(s: str) -> set[str]:
    s = normalize(s)
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def judge(answer: str, correct: str) -> bool:
    """标准答案是否被回答覆盖。"""
    a, c = normalize(answer), normalize(correct)
    if not c:
        return False
    if c in a:
        return True
    if len(c) >= 4 and c[:4] in a:
        return True
    cb = bigrams(c)
    if not cb:
        return False
    ab = bigrams(a)
    return len(cb & ab) / len(cb) >= 0.6


def load_tcmqa() -> list[dict[str, Any]]:
    path = EXT / "_eval" / "TCMQA" / "qa.parquet"
    if not path.is_file():
        return []
    rows = pq.read_table(path).to_pylist()
    out: list[dict[str, Any]] = []
    skipped = 0
    for r in rows:
        topic = str(r.get("topic") or "-")
        if not topic_ok(topic):
            skipped += 1
            continue
        ch = r.get("choices") or {}
        ans = str(r.get("answer") or "").strip()
        correct = "".join(str(ch.get(k, "")) for k in ans if k in ch)
        if not correct:
            continue
        out.append(
            {
                "id": f"tcmqa-{r.get('id')}",
                "question": str(r.get("question") or ""),
                "correct": correct,
                "topic": topic,
                "difficulty": r.get("difficulty_raw"),
            }
        )
    print(f"  TCMQA 过滤掉 {skipped} 道非中医/法规管理题，保留 {len(out)} 道")
    return out


def load_linglan() -> list[dict[str, Any]]:
    d = EXT / "_eval" / "LingLan"
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            opts = r.get("options") or []
            ans = str(r.get("answer") or "").strip()
            correct = ""
            if opts and ans and ans[0] in "ABCDE" and len(ans) == 1:
                idx = ord(ans[0]) - ord("A")
                if 0 <= idx < len(opts):
                    correct = re.sub(r"^[A-E][.、]\s*", "", str(opts[idx]))
            if not correct:
                continue
            out.append(
                {
                    "id": str(r.get("id") or p.stem),
                    "question": str(r.get("question") or ""),
                    "correct": correct,
                    "topic": str(r.get("task") or "-"),
                    "difficulty": None,
                }
            )
    return out


async def answer_one(question: str) -> tuple[str, int, str | None]:
    """跑完整检索 + 生成链路，返回 (回答文本, 检索片段数, 会话 id)。"""
    from app.services import chat_service

    n_hits = 0
    parts: list[str] = []
    cid: str | None = None
    async for event, payload in chat_service.stream_chat(
        conversation_id=None, content=question, image_ids=None
    ):
        if event == "delta":
            parts.append(payload.get("text") or "")
        elif event == "retrieval":
            n_hits = len(payload.get("hits") or [])
        elif event == "meta":
            cid = payload.get("conversation_id") or cid
    return "".join(parts), n_hits, cid


# 把零散的 topic 归到少数几个领域，否则分层抽样会退化成「每题一个 topic」
#（TCMQA 有 1500+ 个 topic，6 题抽 6 个领域，毫无分层意义）。
DOMAIN_RULES: list[tuple[str, str]] = [
    ("基础理论", r"基础|理论|内经|难经|阴阳|五行|藏象|经络|气血津液|病因|病机|防治|养生"),
    ("诊断辨证", r"诊断|辨证|证候|四诊|望诊|闻诊|问诊|切诊|脉|舌|症状|体征"),
    ("方剂治法", r"方剂|治法|治则|方药|经方|中药方|理法方药"),
    ("中药本草", r"中药|本草|药性|功效|炮制|剂量|煎服|配伍|药理|毒性|禁忌|鉴别|性状"),
    ("临床各科", r"内科|外科|妇科|儿科|五官|皮肤|肿瘤|肝胆|脾胃|心|肺|肾|脑|神经|内分泌|血液|消化|呼吸|心血管|泌尿|风湿|糖尿病|高血压|失眠|发热|疼痛"),
    ("针灸推拿", r"针灸|推拿|刺灸|腧穴|经络|穴位|艾灸|拔罐|刮痧"),
    ("经典温病", r"伤寒|金匮|温病|温疫|湿热|六经|卫气营血|三焦|少阴|厥阴|太阳|阳明|少阳|太阴"),
]


def domain_of(topic: str) -> str:
    t = topic or ""
    for name, pat in DOMAIN_RULES:
        if re.search(pat, t):
            return name
    return "其他"


async def run(source: str, limit: int, seed: int) -> None:
    pool = load_tcmqa() if source == "tcmqa" else load_linglan()
    if not pool:
        print(f"题库为空：{source}（先跑 fetch_external_kb）")
        return

    random.seed(seed)
    # 先归领域，再在领域内均衡抽样
    by_domain: dict[str, list[dict]] = {}
    for r in pool:
        r["domain"] = domain_of(r["topic"])
        by_domain.setdefault(r["domain"], []).append(r)
    domains = [d for d, items in by_domain.items() if items]
    picked: list[dict] = []
    per = max(1, limit // max(1, len(domains)))
    for d in sorted(domains):
        items = by_domain[d]
        picked.extend(random.sample(items, min(per, len(items))))
    random.shuffle(picked)
    picked = picked[:limit]

    print(
        f"题库 {source}: {len(pool)} 题，抽样 {len(picked)} 题，"
        f"覆盖 {len(domains)} 个领域（{', '.join(sorted(domains))}）"
    )

    dicts = init_jieba()
    pipeline.set_extractor(EntityExtractor(dicts), dicts)

    rows: list[dict[str, Any]] = []
    created: list[str] = []
    t0 = time.perf_counter()
    try:
        for i, case in enumerate(picked, 1):
            try:
                ans, n_hits, cid = await answer_one(case["question"])
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(picked)}] 失败: {exc}")
                continue
            if cid:
                created.append(cid)
            ok = judge(ans, case["correct"])
            rows.append({**case, "answer": ans, "ok": ok, "hits": n_hits})
            flag = "✓" if ok else "✗"
            print(f"  [{i}/{len(picked)}] {flag} {case['topic'][:14]:<14} 期望={case['correct'][:24]}")
            if i % 10 == 0:
                acc = sum(r["ok"] for r in rows) / len(rows)
                print(f"      当前正确率 {acc:.1%}  用时 {time.perf_counter()-t0:.0f}s")
    finally:
        # 清理评测产生的临时会话。**必须在 finally**：放循环后面的话，
        # 中途 Ctrl-C / 异常就直接跳过 —— 生产库里留下 65 个标题为「t」的
        # 垃圾会话（2026-10-29 实际发生过，主人截图点名）。
        from app.services import conversation_service as conv

        for cid in created:
            try:
                await conv.delete_conversation(cid, hard=True)
            except Exception:  # noqa: BLE001
                pass
        if created:
            print(f"\n已清理 {len(created)} 个临时会话")

    if not rows:
        print("无有效结果")
        return

    acc = sum(r["ok"] for r in rows) / len(rows)
    print(f"\n总正确率: {acc:.1%}  ({sum(r['ok'] for r in rows)}/{len(rows)})  用时 {time.perf_counter()-t0:.0f}s")

    by_topic: dict[str, list[dict]] = {}
    for r in rows:
        by_topic.setdefault(r["topic"], []).append(r)

    lines = ["# 答案质量评测报告", "", f"- 题库：{source}", f"- 样本数：{len(rows)}", f"- **总正确率：{acc:.1%}**", ""]
    lines.append("## 分领域")
    lines.append("")
    lines.append("| 领域 | 样本 | 正确率 |")
    lines.append("|---|---|---|")
    for t, items in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        a = sum(i["ok"] for i in items) / len(items)
        lines.append(f"| {t} | {len(items)} | {a:.1%} |")
    lines.append("")
    fails = [r for r in rows if not r["ok"]]
    lines.append(f"## 未答对（{len(fails)} 条）")
    lines.append("")
    for r in fails[:40]:
        lines.append(f"- **[{r['topic']}]** {r['question'][:80]}")
        lines.append(f"  - 期望：{r['correct'][:60]}")
        lines.append(f"  - 回答：{r['answer'][:120]}")
        lines.append("")

    out = ROOT.parent / "data" / "reports" / f"eval_answer_{source}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告：{out}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60, help="抽样题数")
    ap.add_argument("--source", choices=["tcmqa", "linglan"], default="tcmqa")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    setup_logging("WARNING")
    await init_db()
    await run(args.source, args.limit, args.seed)


if __name__ == "__main__":
    asyncio.run(main())
