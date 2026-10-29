"""检索参数扫参（单进程内循环，模型只加载一次）。

用法：
    python -m scripts.sweep_retrieval
    python -m scripts.sweep_retrieval --param symptom --values 0 0.03 0.06 0.09

结果写到 data/reports/sweep_<param>.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
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

PARAMS = {
    "formula": "rag_formula_boost",
    "disease": "rag_disease_boost",
    "symptom": "rag_symptom_boost",
    "classics": "rag_classics_score_scale",
    "nihai_ratio": "rag_nihai_ratio",
    "top_k_dense": "rag_top_k_dense",
    "top_k_sparse": "rag_top_k_sparse",
    "rerank_top_n": "rag_rerank_top_n",
    "final_k": "rag_final_k",
    "chunks_per_doc": "rag_max_chunks_per_doc",
}


def hit(files: list[str], expect: list[str], k: int) -> bool:
    if not expect:
        return False
    return any(f in files[:k] for f in expect)


def mrr(files: list[str], expect: list[str]) -> float:
    if not expect:
        return 0.0
    for i, f in enumerate(files, start=1):
        if f in expect:
            return 1.0 / i
    return 0.0


async def evaluate(cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        r = await pipeline.retrieve(c["q"])
        files = [str(h.payload.get("source_file", "")) for h in r.hits]
        exp = c.get("expect_files") or []
        rows.append(
            {
                "cat": c.get("category", "-"),
                "h1": hit(files, exp, 1),
                "h3": hit(files, exp, 3),
                "h5": hit(files, exp, 5),
                "mrr": mrr(files, exp),
            }
        )
    n = len(rows) or 1
    out = {
        "hit@1": sum(r["h1"] for r in rows) / n,
        "hit@3": sum(r["h3"] for r in rows) / n,
        "hit@5": sum(r["h5"] for r in rows) / n,
        "mrr": sum(r["mrr"] for r in rows) / n,
    }
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["cat"], []).append(r)
    out["cats"] = {
        cat: sum(i["h5"] for i in items) / len(items) for cat, items in by_cat.items()
    }
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--param", default="symptom", choices=list(PARAMS))
    ap.add_argument("--values", nargs="+", type=float, default=None)
    args = ap.parse_args()

    setup_logging("ERROR")
    await init_db()

    cases = yaml.safe_load(GOLD.read_text(encoding="utf-8")) or []
    dicts = init_jieba()
    pipeline.set_extractor(EntityExtractor(dicts), dicts)
    old_rerank = settings.rag_rerank_backend
    settings.rag_rerank_backend = "none"

    field = PARAMS[args.param]
    base = getattr(settings, field)
    values = args.values if args.values else _default_values(args.param, base)

    print(f"参数 {field}（基准 {base}）扫值：{values}")
    print("样本", len(cases), "条\n")
    results = []
    try:
        for v in values:
            setattr(settings, field, v)
            t0 = time.perf_counter()
            r = await evaluate(cases)
            dt = time.perf_counter() - t0
            results.append((v, r))
            cats = "  ".join(
                f"{k}:{v2:.0%}" for k, v2 in sorted(r["cats"].items())
            )
            print(
                f"  {field}={v:<8} hit@1={r['hit@1']:.1%} hit@3={r['hit@3']:.1%} "
                f"hit@5={r['hit@5']:.1%} MRR={r['mrr']:.3f}  ({dt:.0f}s)"
            )
            print(f"      {cats}")
    finally:
        setattr(settings, field, base)
        settings.rag_rerank_backend = old_rerank

    best = max(results, key=lambda x: (x[1]["hit@5"], x[1]["mrr"]))
    print(f"\n最优：{field}={best[0]}  hit@5={best[1]['hit@5']:.1%}  MRR={best[1]['mrr']:.3f}")

    lines = [
        f"# 扫参报告：{field}",
        "",
        f"- 样本：{len(cases)} 条 gold query（rerank 关）",
        f"- 基准值：{base}",
        "",
        "| 值 | hit@1 | hit@3 | hit@5 | MRR |",
        "|---|---|---|---|---|",
    ]
    for v, r in results:
        mark = " ←" if v == best[0] else ""
        lines.append(
            f"| {v}{mark} | {r['hit@1']:.1%} | {r['hit@3']:.1%} | "
            f"{r['hit@5']:.1%} | {r['mrr']:.3f} |"
        )
    lines.append("")
    lines.append("## 分领域 hit@5")
    lines.append("")
    lines.append("| 值 | " + " | ".join(sorted(best[1]["cats"])) + " |")
    lines.append("|---|" + "---|" * len(best[1]["cats"]))
    for v, r in results:
        lines.append(
            f"| {v} | " + " | ".join(f"{r['cats'][c]:.0%}" for c in sorted(r["cats"])) + " |"
        )
    out = ROOT.parent / "data" / "reports" / f"sweep_{args.param}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告：{out}")


def _default_values(param: str, base: float) -> list[float]:
    if param == "symptom":
        return [0.0, 0.03, 0.06, 0.09, 0.12]
    if param == "formula":
        return [0.08, 0.12, 0.15, 0.20, 0.26]
    if param == "disease":
        return [0.0, 0.06, 0.10, 0.15, 0.20]
    if param == "classics":
        return [0.78, 0.85, 0.92, 1.0]
    if param == "nihai_ratio":
        return [0.4, 0.55, 0.65, 0.8]
    if param == "top_k_dense":
        return [20.0, 40.0, 60.0]
    if param == "top_k_sparse":
        return [20.0, 40.0, 60.0]
    if param == "rerank_top_n":
        return [8.0, 12.0, 16.0, 24.0]
    if param == "final_k":
        return [3.0, 5.0, 8.0]
    if param == "chunks_per_doc":
        return [2.0, 3.0, 5.0, 99.0]
    return [base]


if __name__ == "__main__":
    asyncio.run(main())
