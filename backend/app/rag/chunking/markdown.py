"""Markdown 结构切分。

原则：
1. 按标题层级建树，chunk 归属到完整 section_path
2. 表格 / 引用块 / 代码块不内部切开
3. 超长块按句切分并带 overlap
4. 条文类小节切分时，把父级条文引用带进每个子块（否则子块失去条文号锚点）
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from app.rag.chunking.blocks import (
    Block,
    Section,
    build_section_tree,
    normalize,
    parse_blocks,
    strip_frontmatter,
)
from app.rag.chunking.schema import Chunk

_SENT_SPLIT = re.compile(r"(?<=[。！？；])")
_ARTICLE_TITLE_RE = re.compile(r"条文\s*(\d{1,4})|第\s*(\d{1,4})\s*条")


def _split_text_by_sentence(text: str, target: int, overlap: int) -> list[str]:
    """按句切分，带字符级 overlap。用于单个超长段落。"""
    sents = [s for s in _SENT_SPLIT.split(text) if s]
    if not sents:
        return [text]

    out: list[str] = []
    cur = ""
    for s in sents:
        if cur and len(cur) + len(s) > target:
            out.append(cur)
            tail = cur[-overlap:] if overlap > 0 else ""
            cur = tail + s
        else:
            cur += s
    if cur.strip():
        out.append(cur)
    return out


def _split_table(block: Block, target: int) -> list[str]:
    """表格按行切，每片重复表头两行（表头 + 分隔行）。"""
    lines = block.text.split("\n")
    if len(lines) <= 3:
        return [block.text]
    header = lines[:2]
    rows = lines[2:]

    out: list[str] = []
    cur: list[str] = []
    cur_len = sum(len(x) for x in header)

    for row in rows:
        if cur and cur_len + len(row) > target:
            out.append("\n".join(header + cur))
            cur, cur_len = [], sum(len(x) for x in header)
        cur.append(row)
        cur_len += len(row)
    if cur:
        out.append("\n".join(header + cur))
    return out


def _split_block(block: Block, target: int, max_len: int, overlap: int) -> list[str]:
    """把单个超长块拆成若干不超过 target 的文本片段。"""
    if block.kind == "table":
        return _split_table(block, target)
    if block.kind == "quote":
        # 引用块优先按空行分段
        parts = re.split(r"\n\s*\n", block.text)
        out: list[str] = []
        cur = ""
        for p in parts:
            if len(p) > max_len:
                # 单段仍然超长，退化为按句切
                if cur:
                    out.append(cur)
                    cur = ""
                out.extend(_split_text_by_sentence(p, target, overlap))
                continue
            if cur and len(cur) + len(p) > target:
                out.append(cur)
                cur = p
            else:
                cur = f"{cur}\n\n{p}" if cur else p
        if cur:
            out.append(cur)
        return out
    # 段落 / 列表：按句切
    return _split_text_by_sentence(block.text, target, overlap)


def pack_blocks(
    blocks: list[Block],
    *,
    target: int,
    max_len: int,
    overlap: int,
) -> Iterator[str]:
    """把块贪心打包成不超过 target 的文本片段。"""
    expanded: list[str] = []
    for b in blocks:
        if not b.text.strip():
            continue
        if b.char_len <= max_len:
            expanded.append(b.text)
        else:
            expanded.extend(_split_block(b, target, max_len, overlap))

    cur: list[str] = []
    cur_len = 0
    for piece in expanded:
        if cur and cur_len + len(piece) > target:
            yield "\n\n".join(cur)
            cur, cur_len = [], 0
        cur.append(piece)
        cur_len += len(piece)
    if cur:
        yield "\n\n".join(cur)


def _article_anchor(section: Section) -> str | None:
    """条文类小节：取出可复用的上下文锚点（条文号 + 原文引用）。"""
    m = _ARTICLE_TITLE_RE.search(section.title)
    if not m:
        return None
    anchor = None
    for b in section.direct_blocks:
        if b.kind == "quote":
            anchor = b.text.split("\n\n")[0][:400]
            break
    no = m.group(1) or m.group(2)
    if anchor:
        return f"【条文{no}】\n{anchor}"
    return f"【条文{no}】"


def _render_section(section: Section) -> str:
    """把整节（含子节标题）渲染成带层级的文本。用于小整节合并。"""
    parts: list[str] = []
    if section.heading_line:
        parts.append(section.heading_line)
    parts.extend(b.text for b in section.direct_blocks)
    for child in section.children:
        parts.append(_render_section(child))
    return "\n\n".join(p for p in parts if p.strip())


def split_markdown(
    raw_text: str,
    *,
    source_file: str,
    doc_id: str,
    doc_type: str,
    source_priority: int,
    root_title: str,
    target: int = 700,
    max_len: int = 1200,
    overlap: int = 80,
    min_len: int = 120,
    start_ordinal: int = 0,
) -> list[Chunk]:
    """切分一个新版 Markdown 文件，返回 chunk 列表（未做实体抽取）。"""
    text = strip_frontmatter(normalize(raw_text))
    blocks = parse_blocks(text)
    root = build_section_tree(blocks, root_title)

    chunks: list[Chunk] = []
    ordinal = start_ordinal

    def add(body: str, section: Section, *, header: str = "") -> None:
        nonlocal ordinal
        body = body.strip()
        if not body:
            return
        if header:
            body = f"{header}\n\n{body}"
        chunk = Chunk(
            chunk_id="",
            doc_id=doc_id,
            source_file=source_file,
            doc_type=doc_type,
            source_priority=source_priority,
            text=body,
            section_path=list(section.path),
            heading_level=section.level,
            ordinal=ordinal,
        )
        chunk.chunk_id = _make_id(doc_id, ordinal)
        chunks.append(chunk.finalize())
        ordinal += 1

    def walk(section: Section, anchor: str | None = None) -> None:
        nonlocal ordinal
        # 小整节：含子节一次性成块，保留结构
        if section.total_len() <= max_len and section.children:
            body = _render_section(section)
            if len(body) <= max_len:
                if anchor:
                    body = f"{anchor}\n\n{body}"
                add(body, section)
                return

        # 本节直接内容
        own_anchor = _article_anchor(section) or anchor
        if section.direct_blocks:
            for piece in pack_blocks(
                section.direct_blocks, target=target, max_len=max_len, overlap=overlap
            ):
                header = section.heading_line
                prefix = anchor if (anchor and not section.heading_line) else ""
                add(piece, section, header=f"{prefix}\n{header}".strip() if prefix else header)

        # 子节
        for child in section.children:
            if _ARTICLE_TITLE_RE.search(section.title):
                walk(child, anchor=own_anchor)
            else:
                walk(child, anchor=None)

    # 根节点直接内容（标题前的导语）
    if root.direct_blocks:
        for piece in pack_blocks(
            root.direct_blocks, target=target, max_len=max_len, overlap=overlap
        ):
            add(piece, root, header=f"# {root_title}")

    # 顶级子节。若自身是条文节，其子节要带锚点
    for child in root.children:
        walk(child, anchor=None)

    if min_len > 0:
        chunks = _merge_short(chunks, min_len=min_len, max_len=max_len)

    return chunks


def _merge_short(chunks: list[Chunk], *, min_len: int, max_len: int) -> list[Chunk]:
    """合并过短块。

    SKILL.md 里有大量"标题 + 一句引用"的小节，独立成块会占掉 top-k 名额却没有信息量。
    同文件相邻短块合并，超过 max_len 则停。
    """
    out: list[Chunk] = []
    for ch in chunks:
        if out and out[-1].source_file == ch.source_file and out[-1].char_len < min_len:
            prev = out[-1]
            if prev.char_len + ch.char_len + 2 <= max_len:
                prev.text = f"{prev.text}\n\n{ch.text}"
                prev.formula_names = _union(prev.formula_names, ch.formula_names)
                prev.symptoms = _union(prev.symptoms, ch.symptoms)
                prev.diseases = _union(prev.diseases, ch.diseases)
                prev.acupoints = _union(prev.acupoints, ch.acupoints)
                prev.herbs = _union(prev.herbs, ch.herbs)
                prev.finalize()
                continue
        out.append(ch)
    return out


def _union(a: list[str], b: list[str], limit: int = 12) -> list[str]:
    seen = list(a)
    for x in b:
        if x not in seen:
            seen.append(x)
    return seen[:limit]


def _make_id(doc_id: str, ordinal: int) -> str:
    from app.rag.chunking.schema import make_chunk_id

    return make_chunk_id(doc_id, ordinal)
