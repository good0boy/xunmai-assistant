"""检索效果评测。

用法（backend 目录下）：
    python -m scripts.eval_retrieval            # 不做 LLM 重排，快
    python -m scripts.eval_retrieval --rerank   # 含 LLM 重排，慢但接近线上
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.rag.extract.entities import EntityExtractor  # noqa: E402
from app.rag.extract.tokenize import init_jieba  # noqa: E402
from app.rag.retrieve import pipeline  # noqa: E402

GOLD = ROOT / "tests" / "fixtures" / "gold_queries.yaml"


def _hit_at(hit_files: list[str], expect: list[str], k: int) -> bool:
    if not expect:
        return False
    top = hit_files[:k]
    return any(f in top for f in expect)


def _mrr(hit_files: list[str], expect: list[str]) -> float:
    if not expect:
        return 0.0
    for i, f in enumerate(hit_files, start=1):
        if f in expect:
            return 1.0 / i
    return 0.0


async def run(with_rerank: bool) -> None:
    if not GOLD.exists():
        print(f"评测集不存在: {GOLD}")
        return

    cases = yaml.safe_load(GOLD.read_text(encoding="utf-8")) or []
    dicts = init_jieba()
    pipeline.set_extractor(EntityExtractor(dicts), dicts)

    old = settings.rag_rerank_backend
    if not with_rerank:
        settings.rag_rerank_backend = "none"  # type: ignore[misc]

    rows: list[dict] = []
    try:
        for case in cases:
            q = case["q"]
            expect = case.get("expect_files") or []
            res = await pipeline.retrieve(q)
            files = [str(h.payload.get("source_file", "")) for h in res.hits]
            rows.append(
                {
                    "q": q,
                    "category": case.get("category", "-"),
                    "level": res.level,
                    "n": len(res.hits),
                    "files": files,
                    "h1": _hit_at(files, expect, 1),
                    "h3": _hit_at(files, expect, 3),
                    "h5": _hit_at(files, expect, 5),
                    "mrr": _mrr(files, expect),
                    "top": files[0] if files else "-",
                }
            )
    finally:
        settings.rag_rerank_backend = old  # type: ignore[misc]

    # ---------- 汇总 ----------
    lines = ["# 检索评测报告", ""]
    n = len(rows)
    h1 = sum(r["h1"] for r in rows) / n if n else 0
    h3 = sum(r["h3"] for r in rows) / n if n else 0
    h5 = sum(r["h5"] for r in rows) / n if n else 0
    mrr = sum(r["mrr"] for r in rows) / n if n else 0

    lines.append(f"- 样本数：{n}")
    lines.append(f"- rerank：{'开' if with_rerank else '关'}")
    lines.append(
        "- 指标定义：hit@K 单条查询是 0/1（top-K 内含任一期望文档记 1），"
        "下表是**全部查询的平均值**，即「top-K 命中率」；MRR 是 "
        "「首个命中位次倒数」的平均（0~1 连续值，故用小数）。"
    )
    lines.append(f"- **hit@1：{h1:.1%}**")
    lines.append(f"- **hit@3：{h3:.1%}**")
    lines.append(f"- **hit@5：{h5:.1%}**")
    lines.append(f"- **MRR：{mrr:.3f}**")
    lines.append("")

    # 分类汇总
    cats: dict[str, list[dict]] = {}
    for r in rows:
        cats.setdefault(r["category"], []).append(r)

    lines.append("## 分类")
    lines.append("")
    lines.append("| 类别 | 样本 | hit@5 | MRR |")
    lines.append("|---|---|---|---|")
    for cat, items in cats.items():
        lines.append(
            f"| {cat} | {len(items)} | "
            f"{sum(i['h5'] for i in items) / len(items):.1%} | "
            f"{sum(i['mrr'] for i in items) / len(items):.3f} |"
        )
    lines.append("")

    # 失败用例
    fails = [r for r in rows if not r["h5"]]
    lines.append(f"## 未命中（{len(fails)} 条）")
    lines.append("")
    for r in fails:
        lines.append(f"**{r['q']}**  `level={r['level']}`")
        lines.append(f"- 实际 top3：{r['files'][:3]}")
        lines.append("")

    lines.append("## 全部明细")
    lines.append("")
    lines.append("| 问题 | 级别 | 命中 |MRR| Top1 |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        flag = "✓" if r["h5"] else "✗"
        lines.append(
            f"| {r['q'][:24]} | L{r['level']} | {flag} | {r['mrr']:.2f} | {r['top']} |"
        )

    out = Path("../data/reports/eval_result.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")

    print(f"hit@1={h1:.1%}  hit@3={h3:.1%}  hit@5={h5:.1%}  MRR={mrr:.3f}")
    print(f"报告：{out.resolve()}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerank", action="store_true", help="启用 LLM 重排")
    args = ap.parse_args()

    setup_logging("WARNING")
    await init_db()
    await run(args.rerank)


if __name__ == "__main__":
    asyncio.run(main())
