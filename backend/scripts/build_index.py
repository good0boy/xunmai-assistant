"""构建知识库索引。

用法（backend 目录下）：
    python -m scripts.build_index              # 全量重建
    python -m scripts.build_index --dry-run    # 只切分，不写向量库
    python -m scripts.build_index --only modules/04_jingui.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.session import dispose_db, init_db  # noqa: E402
from app.rag.index.builder import collect_chunks, run_build  # noqa: E402
from app.rag.extract.dicts import Dicts  # noqa: E402
from app.rag.extract.entities import EntityExtractor  # noqa: E402
from app.rag.extract.tokenize import init_jieba  # noqa: E402
from app.rag.chunking.dedupe import dedupe  # noqa: E402


def _resolve_kb_root() -> Path:
    p = Path(settings.kb_root)
    if p.is_absolute():
        return p
    return (ROOT / p).resolve()


async def dry_run(kb_root: Path, only_file: str | None, out_path: Path | None = None) -> None:
    dicts = init_jieba()
    extractor = EntityExtractor(dicts)
    chunks, stats = collect_chunks(kb_root, dicts, extractor, only_file=only_file)
    kept, dropped = dedupe(chunks, stopwords=dicts.stopwords)

    lines: list[str] = []
    w = lines.append
    w(f"文件数: {len(stats['files'])}")
    w(f"切分产出: {len(chunks)} 块")
    w(f"去重丢弃: {dropped} 块")
    w(f"最终: {len(kept)} 块")
    w("")

    w(f"{'文件':<44} {'类型':<12} {'块数':>6} {'字符':>10}")
    for f in stats["files"]:
        w(f"{f['file']:<44} {f['doc_type']:<12} {f['chunks']:>6} {f['chars']:>10,}")

    cr = stats.get("case_report")
    if cr:
        w("")
        w(
            f"医案表: {cr.get('rows')} 行, 疑似错标 {cr.get('suspect_tags')}, "
            f"纠正 {cr.get('fixed_tags')}, 聚合卡 {cr.get('agg_cards')}"
        )

    lens = sorted(c.char_len for c in kept)
    if lens:
        w("")
        w(
            f"chunk 长度: min={lens[0]} p50={lens[len(lens) // 2]} "
            f"p90={lens[int(len(lens) * 0.9)]} max={lens[-1]}"
        )
        too_long = [c for c in kept if c.char_len > settings.kb_max_chunk_chars]
        too_short = [c for c in kept if c.char_len < settings.kb_min_chunk_chars]
        w(f"超长块(>{settings.kb_max_chunk_chars}): {len(too_long)}")
        for c in sorted(too_long, key=lambda x: -x.char_len)[:12]:
            w(f"   {c.char_len:>5}  {c.source_file}  {c.section_path_str[:50]}")
        w(f"过短块(<{settings.kb_min_chunk_chars}): {len(too_short)}")
        for c in too_short[:10]:
            w(f"   {c.char_len:>5}  {c.source_file}  {c.text[:40]!r}")

    w("")
    w("--- 抽样 4 块 ---")
    sample_idx = [0, len(kept) // 3, len(kept) // 2, len(kept) - 1]
    for i in sample_idx:
        if i >= len(kept):
            continue
        ch = kept[i]
        w("")
        w(f"[{ch.doc_type}] {ch.source_file} | {ch.section_path_str[:60]}")
        w(f"  实体: 方={ch.formula_names[:3]} 症={ch.symptoms[:3]} 病={ch.diseases[:2]}")
        w(f"  正文: {ch.text[:200]}")

    text = "\n".join(lines)
    target = out_path or Path("../data/reports/dryrun.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"报告已写入 {target.resolve()}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只切分，不写入向量库")
    ap.add_argument("--only", default=None, help="只处理匹配该子串的文件")
    ap.add_argument("--out", default=None, help="dry-run 报告输出路径")
    ap.add_argument("--no-recreate", action="store_true", help="不重建集合（增量追加）")
    ap.add_argument(
        "--groups",
        default="all",
        help="要构建的分组：all / nihai / classics / judges（逗号分隔）。"
        "分组各自独立建集，可以只补建其中一组而不动其它组。",
    )
    args = ap.parse_args()

    setup_logging(settings.log_level)
    kb_root = _resolve_kb_root()
    if not kb_root.exists():
        print(f"知识库目录不存在: {kb_root}")
        sys.exit(1)

    await init_db()

    if args.dry_run:
        await dry_run(
            kb_root, args.only, Path(args.out) if args.out else None
        )
        await dispose_db()
        return

    groups = None if args.groups == "all" else {g.strip() for g in args.groups.split(",") if g.strip()}

    try:
        result = await run_build(
            kb_root,
            only_file=args.only,
            recreate=not args.no_recreate,
            groups=groups,
        )
    finally:
        # 必须显式关闭连接池，否则 WAL 可能不落盘
        await dispose_db()

    print("\n构建完成：")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print("\n报告：../data/reports/build_report.md")


if __name__ == "__main__":
    asyncio.run(main())
