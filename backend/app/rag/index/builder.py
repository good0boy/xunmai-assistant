"""知识库索引构建主流程。

流程：遍历文件 → 结构切分 → 实体抽取 → 去重 → 向量化 → 写入 Qdrant → 落 kb_meta。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.paths import resolve_path
from app.rag.chunking.cnnum import extract_chapter_no
from app.rag.chunking.dedupe import dedupe
from app.rag.chunking.markdown import split_markdown
from app.rag.chunking.schema import Chunk, make_doc_id
from app.rag.chunking.table import build_case_chunks
from app.rag.extract.dicts import Dicts
from app.rag.extract.entities import EntityExtractor
from app.rag.extract.tokenize import (
    init_jieba,
    keywords,
    sparse_signature,
    to_sparse,
)
from app.rag.index import qdrant_store, sources
from app.rag.index.meta import (
    corpus_hash,
    expected_build_version,
    read_meta,
    write_meta,
)
from app.rag.index.qdrant_store import DENSE_VECTOR, SPARSE_VECTOR

logger = logging.getLogger(__name__)

# 路径前缀 → (doc_type, source_priority, 根标题)
DOC_MAP: list[tuple[str, str, int, str]] = [
    ("SKILL.md", "skill_card", 1, "倪海厦技能主文件"),
    ("expression_style.md", "style", 1, "倪师表达风格"),
    ("distilled_cases.md", "case", 4, "医案清单"),
    ("cases/00_merged_table.md", "case_table", 4, "医案合并表"),
    ("cases/", "case", 4, "分类医案"),
    ("modules/", "module", 2, "知识模块"),
    ("references/distilled/", "distilled", 3, "蒸馏速查"),
    ("references/research/", "research", 5, "研究资料"),
    ("references/audit/", "audit", 5, "审计清单"),
]

SKIP_NAMES = {"README.md", "README_EN.MD", "CHANGELOG.md", "index.html", "logo.jpg", ".gitignore"}
SKIP_DIRS = {"docs", ".git"}

CASE_TAG_FIXES: dict[str, dict[str, str]] = {
    # 已知错标：正文明确写了红斑狼疮，原表标成肺癌
    "193": {"tag": "红斑狼疮", "reason": "正文明确，原表标签误标"},
}


def classify(rel_path: str) -> tuple[str, int, str] | None:
    for prefix, doc_type, prio, root in DOC_MAP:
        if prefix.endswith("/"):
            if rel_path.startswith(prefix):
                return doc_type, prio, root
        elif rel_path == prefix:
            return doc_type, prio, root
    return None


def iter_files(kb_root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(kb_root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(kb_root).as_posix()
        if p.name in SKIP_NAMES:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(kb_root).parts):
            continue
        if p.suffix.lower() not in (".md", ".txt"):
            continue
        if classify(rel) is None:
            continue
        out.append(p)
    return out


def _build_prefix(ch: Chunk) -> str:
    """向量化前缀：把章节路径与实体压缩成一行，显著提升表格行/短块的召回。"""
    bits: list[str] = []
    if ch.section_path:
        bits.append("/".join(ch.section_path[-3:]))
    if ch.formula_names:
        bits.append("方:" + "、".join(ch.formula_names[:4]))
    if ch.symptoms:
        bits.append("症:" + "、".join(ch.symptoms[:4]))
    if ch.diseases:
        bits.append("病:" + "、".join(ch.diseases[:3]))
    if ch.article_no:
        bits.append(f"条文{ch.article_no}")
    return f"【{'｜'.join(bits)}】\n" if bits else ""


def enrich(ch: Chunk, extractor: EntityExtractor, dicts: Dicts) -> None:
    e = extractor.extract(ch.text)
    ch.formula_names = e.formulas
    ch.acupoints = e.acupoints
    ch.diseases = e.diseases
    ch.symptoms = e.symptoms
    ch.herbs = e.herbs
    if e.article_nos:
        ch.article_no = e.article_nos[0]
        ch.article_no_note = "倪师讲课编号，非宋本编号"
    ch.keywords = keywords(ch.text, dicts.stopwords)

    # 金匮篇目：源文件并非按篇号顺序排列，这里按标题解析逻辑篇号
    if ch.source_file.endswith("04_jingui.md"):
        for seg in ch.section_path:
            no = extract_chapter_no(seg)
            if no:
                ch.chapter_no = no
                break

    if settings.kb_embed_context_prefix:
        ch.embed_text = _build_prefix(ch) + ch.text
    else:
        ch.embed_text = ch.text


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"xunmai:{chunk_id}"))


def collect_chunks(
    kb_root: Path, dicts: Dicts, extractor: EntityExtractor, *, only_file: str | None = None
) -> tuple[list[Chunk], dict[str, Any]]:
    files = iter_files(kb_root)
    if only_file:
        files = [f for f in files if only_file in f.as_posix()]
        if not files:
            raise FileNotFoundError(f"未匹配到文件: {only_file}")

    stats: dict[str, Any] = {"files": [], "case_report": None}
    all_chunks: list[Chunk] = []

    for path in files:
        rel = path.relative_to(kb_root).as_posix()
        meta = classify(rel)
        if meta is None:
            continue
        doc_type, prio, root_title = meta
        doc_id = make_doc_id(rel)
        raw = path.read_text(encoding="utf-8", errors="ignore")

        if doc_type == "case_table":
            chunks, report = build_case_chunks(
                raw,
                source_file=rel,
                doc_id=doc_id,
                chunk_id_fn=lambda d, o: _mk_id(d, o),
                tag_fixes=CASE_TAG_FIXES,
                detect_diseases=extractor.diseases_of,
                detect_formulas=extractor.formulas_of,
                max_len=settings.kb_max_chunk_chars,
            )
            stats["case_report"] = report
        else:
            chunks = split_markdown(
                raw,
                source_file=rel,
                doc_id=doc_id,
                doc_type=doc_type,
                source_priority=prio,
                root_title=root_title,
                target=settings.kb_target_chunk_chars,
                max_len=settings.kb_max_chunk_chars,
                overlap=settings.kb_overlap_chars,
                min_len=settings.kb_min_chunk_chars,
            )

        for ch in chunks:
            enrich(ch, extractor, dicts)
            ch.source_group = sources.NIHAI_GROUP
            ch.origin = sources.detect_origin(ch.text, rel)
            # 条文号补全：正文里的「#### 条文105：」最精确，
            # 章节路径只能给出范围（「条文1-129」）
            if not ch.article_no:
                ch.article_no = (
                    sources.extract_article_no(ch.section_path_str, ch.text) or None
                )

        all_chunks.extend(chunks)
        stats["files"].append(
            {
                "file": rel,
                "doc_type": doc_type,
                "priority": prio,
                "chunks": len(chunks),
                "chars": sum(c.char_len for c in chunks),
            }
        )
        logger.info("%s → %d chunks", rel, len(chunks))

    return all_chunks, stats


def _mk_id(doc_id: str, ordinal: int) -> str:
    from app.rag.chunking.schema import make_chunk_id

    return make_chunk_id(doc_id, ordinal)


def collect_external_chunks(
    dicts: Dicts, extractor: EntityExtractor, *, root: Path | None = None
) -> dict[str, list[Chunk]]:
    """采集外部来源：经典原文与判语，各自独立成集。

    - classics：《本草纲目》《千金要方》等 21 本 + 汉唐方书
    - judges  ：倪师原话字幕级判语，每条带 B 站出处

    返回 {group: chunks}，group 决定写到哪个集合。
    """
    ext_root = root or resolve_path(settings.kb_external_root)
    out: dict[str, list[Chunk]] = {}

    if not ext_root.is_dir():
        logger.warning("外部知识库目录不存在，跳过: %s", ext_root)
        return out

    # ---------- 经典原文 ----------
    classics: list[Chunk] = []
    for sub in ("zhongyi-books", "qmzz-ht-formulas"):
        d = ext_root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            raw = p.read_text(encoding="utf-8", errors="ignore")
            if len(raw.strip()) < 500:
                continue
            rel = f"classics/{p.stem}.md"
            book = p.stem
            chunks = split_markdown(
                raw,
                source_file=rel,
                doc_id=make_doc_id(rel),
                doc_type="classic",
                source_priority=settings.kb_classics_priority,
                root_title=book,
                target=settings.kb_target_chunk_chars,
                max_len=settings.kb_max_chunk_chars,
                overlap=settings.kb_overlap_chars,
                min_len=settings.kb_min_chunk_chars,
            )
            origin = sources.detect_origin(book, p.name)
            for ch in chunks:
                enrich(ch, extractor, dicts)
                ch.source_group = sources.CLASSICS_GROUP
                ch.origin = origin
            classics.extend(chunks)
            logger.info("%s → %d chunks (origin=%s)", rel, len(chunks), origin or "-")
    if classics:
        out[sources.CLASSICS_GROUP] = classics

    # ---------- 判语 ----------
    judge_file = ext_root / "nihai-judges" / "corpus-rag.md"
    if judge_file.is_file():
        raw = judge_file.read_text(encoding="utf-8", errors="ignore")
        rel = "judges/corpus-rag.md"
        chunks = split_markdown(
            raw,
            source_file=rel,
            doc_id=make_doc_id(rel),
            doc_type="judge",
            source_priority=settings.kb_judges_priority,
            root_title="倪师判语",
            target=settings.kb_target_chunk_chars,
            max_len=settings.kb_max_chunk_chars,
            overlap=settings.kb_overlap_chars,
            min_len=settings.kb_min_chunk_chars,
        )
        kept = 0
        for ch in chunks:
            cites = sources.parse_judge_cites(ch.text)
            if not cites:
                # 没有出处的判语不可回放核对，直接丢弃
                continue
            enrich(ch, extractor, dicts)
            ch.source_group = sources.JUDGES_GROUP
            ch.origin = sources.detect_origin(ch.text, rel)
            ch.article_no = sources.extract_article_no(ch.section_path_str, ch.text) or None
            ch.judge_cites = cites
            kept += 1
        if kept:
            logger.info("%s → %d chunks (带出处)", rel, kept)
            out[sources.JUDGES_GROUP] = [c for c in chunks if c.judge_cites]

    return out


def _count_zero_in_batch(client: Any, name: str, ids: list[Any]) -> int:
    """检查这一批刚写入的点里有多少个 dense 向量是全零。"""
    import math

    if not ids:
        return 0
    try:
        pts = client.retrieve(
            collection_name=name, ids=ids, with_vectors=True, with_payload=False
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("批次抽查读取失败: %s", exc)
        return 0
    zero = 0
    for p in pts:
        vec = p.vector.get(DENSE_VECTOR) if isinstance(p.vector, dict) else p.vector
        if not vec:
            continue
        if math.sqrt(sum(float(x) * float(x) for x in vec)) < 1e-6:
            zero += 1
    return zero


def _find_zero_vector_ids(client: Any, name: str) -> list[Any]:
    """扫出集合里 dense 向量为全零的点 id。"""
    import math

    zero_ids: list[Any] = []
    offset = None
    while True:
        pts, offset = client.scroll(
            name, limit=1024, offset=offset, with_vectors=True, with_payload=False
        )
        for p in pts:
            vec = p.vector.get(DENSE_VECTOR) if isinstance(p.vector, dict) else p.vector
            if not vec:
                continue
            if math.sqrt(sum(float(x) * float(x) for x in vec)) < 1e-6:
                zero_ids.append(p.id)
        if offset is None or not pts:
            break
    return zero_ids


def _repair_zero_vectors(
    client: Any, name: str, chunks: list[Chunk]
) -> int:
    """把零向量点用内存里的原始向量重新写入。

    为什么要自愈：零向量点会让 dense 召回返回一批 0 分的无关文档，
    把真正相关的挤出去，而且不报错。根因（ONNX 静默失败 / Qdrant 写入
    竞态）在不同环境下表现不一致，与其赌不复现，不如写完扫一遍、
    发现就修 —— 重写的向量来自内存里的 embed 结果，一定正确。
    """
    from qdrant_client.http import models as qm

    from app.rag.index.embedder import embed_documents

    zero_ids = _find_zero_vector_ids(client, name)
    if not zero_ids:
        return 0
    zero_set = set(zero_ids)
    logger.warning("%s 有 %d 个零向量点，开始重写", name, len(zero_ids))

    by_point = {_point_id(c.chunk_id): c for c in chunks}
    targets = [by_point[pid] for pid in zero_ids if pid in by_point]
    if not targets:
        logger.error("%s 的零向量点找不到对应 chunk，无法修复", name)
        return len(zero_ids)

    for i in range(0, len(targets), 64):
        part = targets[i : i + 64]
        vectors = embed_documents([c.embed_text for c in part])
        points = []
        for ch, vec in zip(part, vectors, strict=True):
            idx, val = to_sparse(ch.embed_text)
            points.append(
                qm.PointStruct(
                    id=_point_id(ch.chunk_id),
                    vector={
                        DENSE_VECTOR: vec,
                        SPARSE_VECTOR: qm.SparseVector(indices=idx, values=val),
                    },
                    payload=ch.to_payload(),
                )
            )
        client.upsert(collection_name=name, points=points, wait=True)
    logger.warning("%s 已重写 %d 个零向量点", name, len(targets))
    return len(targets)


def _embed_dim_actual() -> int:
    """实测向量维度（不能信配置里的兜底值）。"""
    try:
        from app.rag.index.embedder import dim as embed_dim

        return int(embed_dim())
    except Exception:  # noqa: BLE001
        return int(settings.embed_dim)


def _check_zero_vectors(name: str, sample: int = 0) -> int:
    """统计集合里 dense 向量为全零的点数。

    ONNX 推理或 Qdrant 写入出问题时可能静默产生全零向量 ——
    索引看起来建好了，检索却退化成「dense 召回一堆 0 分的任意文档」，
    指标掉一大截却查不出原因。

    **必须全量扫描**（sample=0）：实测零向量是间歇性的，比例每次都不同
    （8.5% / 23% / 28.8%），抽查 200 个会大量漏检。

    **抽查本身失败时必须 fail-closed（抛异常）**：这个函数是防护网，
    防护网自己漏了就等于没有防护。之前这里是 `return 0`（= 放行），
    抽查失败会静默放过一个坏索引。
    """
    import math

    from app.rag.index import qdrant_store

    client = qdrant_store.get_client()
    zero = 0
    offset = None
    try:
        while True:
            pts, offset = client.scroll(
                name,
                limit=1024,
                offset=offset,
                with_vectors=True,
                with_payload=False,
            )
            for p in pts:
                vec = p.vector.get(DENSE_VECTOR) if isinstance(p.vector, dict) else p.vector
                if not vec:
                    continue
                if math.sqrt(sum(float(x) * float(x) for x in vec)) < 1e-6:
                    zero += 1
            if offset is None or not pts:
                break
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"集合 {name} 的向量全量扫描失败，无法确认索引是否可用: {exc}"
        ) from exc
    return zero


def upsert_chunks(
    chunks: list[Chunk],
    *,
    batch: int = 64,
    recreate: bool = True,
    collection: str | None = None,
) -> int:
    from qdrant_client.http import models as qm

    from app.rag.index.embedder import embed_documents

    name = collection or settings.qdrant_collection
    qdrant_store.ensure_collection(recreate=recreate, collection=name)
    client = qdrant_store.get_client()

    total = 0
    for i in range(0, len(chunks), batch):
        part = chunks[i : i + batch]
        vectors = embed_documents([c.embed_text for c in part])
        points = []
        for ch, vec in zip(part, vectors, strict=True):
            idx, val = to_sparse(ch.embed_text)
            points.append(
                qm.PointStruct(
                    id=_point_id(ch.chunk_id),
                    vector={
                        DENSE_VECTOR: vec,
                        SPARSE_VECTOR: qm.SparseVector(indices=idx, values=val),
                    },
                    payload=ch.to_payload(),
                )
            )
        client.upsert(collection_name=name, points=points, wait=True)
        total += len(points)

        # 逐批抽查：定位零向量到底出在哪一批。
        # 一次性全量抽查只能告诉你「有 N 个零向量」，说不出是哪批坏的。
        zero_in_batch = _count_zero_in_batch(client, name, [p.id for p in points])
        if zero_in_batch:
            logger.error(
                "批次 %d（累计 %d/%d）写入后有 %d 个零向量",
                i // batch + 1,
                total,
                len(chunks),
                zero_in_batch,
            )
        logger.info("upsert %s %d/%d", name, total, len(chunks))

    # 写完抽查：全零向量说明 ONNX 推理静默失败，索引等于废的。
    # 抽查失败也要炸 —— 坏数据留在库里比报错更难查。
    if total:
        try:
            zero = _check_zero_vectors(name)
        except RuntimeError:
            qdrant_store.clear_points(name)
            raise
        if zero:
            # 先自愈（用内存里的原始向量重写），修完再判定
            repaired = _repair_zero_vectors(client, name, chunks)
            zero = _check_zero_vectors(name)
            if zero:
                qdrant_store.clear_points(name)
                raise RuntimeError(
                    f"集合 {name} 仍有 {zero}/{total} 个 dense 向量为全零 —— "
                    f"已尝试重写 {repaired} 个仍无效。"
                    f"请调小 settings.embed_batch 后重建。"
                )
            logger.warning("%s 自愈后向量抽查通过（重写了 %d 个）", name, repaired)
        else:
            logger.info("%s 向量抽查通过（非零）", name)
    return total


def build_report(
    stats: dict[str, Any],
    chunks: list[Chunk],
    *,
    dropped: int,
    elapsed: float,
    out_path: Path,
) -> None:
    lines: list[str] = ["# 知识库构建报告", ""]
    lines.append(f"- 总 chunk 数：**{len(chunks)}**")
    lines.append(f"- 去重丢弃：{dropped}")
    lines.append(f"- 耗时：{elapsed:.1f}s")
    lines.append(f"- 构建版本：`{expected_build_version()}`")
    lines.append("")

    lines.append("## 各文件切分结果")
    lines.append("")
    lines.append("| 文件 | 类型 | 优先级 | chunk 数 | 字符数 |")
    lines.append("|---|---|---|---|---|")
    for f in stats["files"]:
        lines.append(
            f"| {f['file']} | {f['doc_type']} | {f['priority']} | {f['chunks']} | {f['chars']:,} |"
        )
    lines.append("")

    cr = stats.get("case_report")
    if cr:
        lines.append("## 医案表处理")
        lines.append("")
        lines.append(f"- 数据行：{cr.get('rows')}")
        lines.append(f"- 列数：{len(cr.get('columns', []))}")
        lines.append(f"- 疑似错标（正文反推）：{cr.get('suspect_tags')}")
        lines.append(f"- 已纠正标签：{cr.get('fixed_tags')}")
        lines.append(f"- 聚合卡：{cr.get('agg_cards')}")
        lines.append("")

    # 超长块
    longest = sorted(chunks, key=lambda c: c.char_len, reverse=True)[:15]
    lines.append("## 最长 chunk Top15")
    lines.append("")
    lines.append("| 字符数 | 来源 | 章节 |")
    lines.append("|---|---|---|")
    for c in longest:
        sec = c.section_path_str[-40:] if c.section_path_str else "-"
        lines.append(f"| {c.char_len} | {c.source_file} | {sec} |")
    lines.append("")

    # doc_type 分布
    dist = Counter(c.doc_type for c in chunks)
    lines.append("## 类型分布")
    lines.append("")
    for k, v in dist.most_common():
        lines.append(f"- {k}: {v}")

    # 金匮篇号覆盖
    jingui = [c for c in chunks if c.source_file.endswith("04_jingui.md")]
    if jingui:
        nos = sorted({c.chapter_no for c in jingui if c.chapter_no})
        lines.append("")
        lines.append("## 金匮篇号覆盖")
        lines.append("")
        lines.append(f"- 已识别篇号：{nos}")
        missing = [n for n in range(1, 24) if n not in nos]
        lines.append(f"- 缺失篇号：{missing if missing else '无'}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


async def run_build(
    kb_root: Path,
    *,
    only_file: str | None = None,
    recreate: bool = True,
    report_dir: Path | None = None,
    groups: set[str] | None = None,
) -> dict[str, Any]:
    """构建索引。

    groups=None 表示全建；否则只建指定分组（各自的集合独立，互不影响）。
    """
    t0 = time.perf_counter()
    dicts = init_jieba()
    extractor = EntityExtractor(dicts)

    want_nihai = groups is None or sources.NIHAI_GROUP in groups
    want_ext = groups is None or (
        sources.CLASSICS_GROUP in groups or sources.JUDGES_GROUP in groups
    )

    count = 0
    kept: list[Chunk] = []
    stats: dict[str, Any] = {"files": [], "case_report": None}
    dropped = 0
    if want_nihai:
        logger.info("collecting chunks from %s", kb_root)
        all_chunks, stats = collect_chunks(kb_root, dicts, extractor, only_file=only_file)
        logger.info("collected %d chunks", len(all_chunks))

        kept, dropped = dedupe(all_chunks, stopwords=dicts.stopwords, max_distance=3)
        logger.info("after dedupe: %d (dropped %d)", len(kept), dropped)
        count = upsert_chunks(kept, recreate=recreate)

    # ---------- 外部来源：各自独立建集 ----------
    external: dict[str, int] = {}
    if want_ext and not only_file:
        ext_groups = collect_external_chunks(dicts, extractor)
        for group, chunks in ext_groups.items():
            if groups is not None and group not in groups:
                continue
            collection = (
                settings.qdrant_collection_classics
                if group == sources.CLASSICS_GROUP
                else settings.qdrant_collection_judges
            )
            ext_kept, _ = dedupe(chunks, stopwords=dicts.stopwords, max_distance=3)
            n = upsert_chunks(ext_kept, recreate=recreate, collection=collection)
            external[group] = n
            logger.info("%s → %s: %d chunks", group, collection, n)

    elapsed = time.perf_counter() - t0

    # 元信息：**只更新本次真正构建的分组**，其余保留旧值。
    # 否则 `--groups classics` 会把 doc_count 写成 0、把判语组的记录抹掉。
    meta: dict[str, Any] = {
        "build_version": expected_build_version(),
        "embed_model": settings.embed_model,
        "embed_dim": str(_embed_dim_actual()),
        "sparse_signature": sparse_signature(dicts),
        "query_prefix": settings.embed_query_prefix,
        "corpus_hash": corpus_hash(),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if want_nihai:
        meta["doc_count"] = str(count)
    if external:
        try:
            old_groups = json.loads(await read_meta("external_groups") or "{}")
        except (TypeError, ValueError):
            old_groups = {}
        if not isinstance(old_groups, dict):
            old_groups = {}
        old_groups.update(external)
        meta["external_groups"] = json.dumps(old_groups, ensure_ascii=False)
        meta["external_doc_count"] = str(sum(int(v) for v in old_groups.values()))
    await write_meta(meta)

    from app.core.paths import PROJECT_ROOT

    rd = report_dir or (PROJECT_ROOT.parent / "data" / "reports")
    build_report(stats, kept if want_nihai else [], dropped=dropped, elapsed=elapsed, out_path=rd / "build_report.md")

    return {
        "chunks": count,
        "external": external,
        "dropped": dropped,
        "elapsed_s": round(elapsed, 1),
        "files": len(stats["files"]),
    }
