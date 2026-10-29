"""医案表格解析与三级表示。

1257 例医案是一张 12 列大表。整表切碎会丢列语义，不切会炸 chunk。
解法是三级产物：
  1. 表结构卡  —— 1 个块，说明这批数据是什么
  2. 行级 chunk —— 每行一块，只输出非空列
  3. 聚合卡     —— 按病名 / 方名统计，解决"行级块丢全局视角"的问题
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from app.rag.chunking.schema import Chunk

_CELL_SPLIT = re.compile(r"(?<!\\)\|")

# 列名归一：源表列名带括号注释，检索时不友好
_COL_ALIAS = {
    "序号": "序号",
    "日期": "日期",
    "患者": "患者",
    "主要诊断(中医)": "诊断",
    "中医病因分析(关键病机)": "病机",
    "西医背景与观点": "西医背景",
    "具体方剂剂量/治疗组成": "方剂",
    "针灸方案": "针灸",
    "中医治疗方法(原则)": "治法",
    "疗程/结果": "结果",
    "生活医嘱": "医嘱",
    "倪海厦核心观点/评论": "倪师观点",
}

# 行级 chunk 里值得保留的列（其余列对检索贡献低且占字数）
_KEEP_COLS = ["日期", "患者", "诊断", "病机", "西医背景", "方剂", "针灸", "治法", "结果", "医嘱", "倪师观点"]


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in _CELL_SPLIT.split(s)]


def parse_md_table(text: str) -> tuple[list[str], list[list[str]]]:
    """返回 (表头, 数据行)。自动跳过对齐分隔行。"""
    lines = [ln for ln in text.split("\n") if ln.strip().startswith("|")]
    if len(lines) < 2:
        return [], []

    header = split_row(lines[0])
    rows: list[list[str]] = []
    for ln in lines[1:]:
        cells = split_row(ln)
        joined = "".join(cells)
        # 分隔行：全是 -、: 和空白
        if joined and set(joined) <= set("-: "):
            continue
        if len(cells) < 2:
            continue
        rows.append(cells)
    return header, rows


def _norm_header(header: list[str]) -> list[str]:
    out = []
    for h in header:
        key = h.strip()
        out.append(_COL_ALIAS.get(key, key))
    return out


def _non_empty_pairs(cols: list[str], cells: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for i, col in enumerate(cols):
        if i >= len(cells):
            break
        val = cells[i].strip()
        if not val or val in ("---", "-", "同上", "未提及"):
            continue
        pairs.append((col, val))
    return pairs


def _clean_cell(val: str, limit: int = 600) -> str:
    val = re.sub(r"\s+", " ", val).strip()
    if len(val) > limit:
        val = val[:limit] + "…"
    return val


def row_to_text(
    cols: list[str],
    cells: list[str],
    serial: str,
    *,
    tag_note: str | None = None,
) -> str:
    """单行渲染成可检索文本。头部是索引行，正文按列展开。"""
    diag = ""
    for i, c in enumerate(cols):
        if c == "诊断" and i < len(cells):
            diag = cells[i].strip()
            break

    head_bits = [f"医案#{serial}"]
    if diag:
        head_bits.append(f"诊断:{diag}")
    if tag_note:
        head_bits.append(f"（{tag_note}）")

    lines = [f"【{'｜'.join(head_bits)}】"]
    for col, val in _non_empty_pairs(cols, cells):
        if col == "序号":
            continue
        lines.append(f"{col}: {_clean_cell(val)}")
    return "\n".join(lines)


def build_case_chunks(
    table_text: str,
    *,
    source_file: str,
    doc_id: str,
    chunk_id_fn: Any,
    tag_fixes: dict[str, dict[str, str]] | None = None,
    detect_diseases: Any = None,
    detect_formulas: Any = None,
    max_len: int = 1200,
    agg_min_count: int = 5,
    start_ordinal: int = 0,
) -> tuple[list[Chunk], dict[str, Any]]:
    """返回 (chunks, 统计报告)。"""
    header, rows = parse_md_table(table_text)
    if not header:
        return [], {"rows": 0, "error": "no table found"}

    cols = _norm_header(header)
    tag_fixes = tag_fixes or {}
    ordinal = start_ordinal
    chunks: list[Chunk] = []
    report: dict[str, Any] = {
        "rows": len(rows),
        "columns": cols,
        "suspect_tags": 0,
        "fixed_tags": 0,
        "agg_cards": 0,
    }

    # ---------- 1. 表结构卡 ----------
    meta_text = (
        f"【医案总表说明】\n"
        f"本表为倪海厦医师 1257 例医案的结构化汇总，共 {len(rows)} 条记录，"
        f"{len(cols)} 列：{'、'.join(cols)}。\n"
        f"覆盖 2005-2009 年。高频诊断包括癌、肝病、乳癌、肝癌、肺癌、失眠、"
        f"心脏病、糖尿病、血癌、肝硬化、脑瘤等。\n"
        f"说明：原表部分行分类标签存在错标（已按正文更正并标注），检索时应以正文描述为准。\n"
        f"用途：按诊断或方剂检索具体医案，了解倪师对某类病证的实际处置与转归。"
    )
    c = Chunk(
        chunk_id=chunk_id_fn(doc_id, ordinal),
        doc_id=doc_id,
        source_file=source_file,
        doc_type="case_meta",
        source_priority=4,
        text=meta_text,
        section_path=["医案合并表", "总表说明"],
        heading_level=1,
        ordinal=ordinal,
    )
    chunks.append(c.finalize())
    ordinal += 1

    # ---------- 2. 行级 chunk ----------
    by_disease: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    by_formula: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    serial_list: list[str] = []

    for cells in rows:
        serial = cells[0].strip() if cells else ""
        if not serial:
            continue
        serial_list.append(serial)

        raw_diag = ""
        for i, col in enumerate(cols):
            if col == "诊断" and i < len(cells):
                raw_diag = cells[i].strip()
                break

        fixed_diag = raw_diag
        quality = "ok"
        tag_note = None

        fix = tag_fixes.get(serial)
        if fix:
            fixed_diag = fix.get("tag", raw_diag)
            quality = "fixed"
            tag_note = f"原表标签:{raw_diag}，已纠正"
            report["fixed_tags"] += 1
        elif detect_diseases and raw_diag:
            found = detect_diseases(" ".join(cells))
            if found and not any(x in raw_diag for x in found):
                fixed_diag = found[0]
                quality = "suspect"
                tag_note = f"原表标签:{raw_diag}，正文作:{fixed_diag}"
                report["suspect_tags"] += 1

        body = row_to_text(cols, cells, serial, tag_note=tag_note)
        # 超长行按列边界二次切分，保留头部索引行
        texts = _split_row_text(body, cols, cells, serial, max_len)

        for t in texts:
            ch = Chunk(
                chunk_id=chunk_id_fn(doc_id, ordinal),
                doc_id=doc_id,
                source_file=source_file,
                doc_type="case_row",
                source_priority=4,
                text=t,
                section_path=["医案合并表", f"医案#{serial}"],
                heading_level=2,
                ordinal=ordinal,
                case_serial=serial,
                case_tag_raw=raw_diag or None,
                case_tag_fixed=fixed_diag or None,
                tag_quality=quality,
                row_raw=cells,
            )
            chunks.append(ch.finalize())
            ordinal += 1

        if fixed_diag:
            cleaned = _clean_disease(fixed_diag)
            if cleaned and cleaned not in _BAD_DISEASE and len(cleaned) >= 2:
                by_disease[cleaned].append((serial, cells))
        if detect_formulas:
            for f in detect_formulas(" ".join(cells)):
                by_formula[f].append((serial, cells))

    # ---------- 3. 聚合卡 ----------
    ordinal = _emit_agg_cards(
        chunks,
        ordinal,
        by_disease,
        cols,
        kind="病名",
        doc_id=doc_id,
        source_file=source_file,
        chunk_id_fn=chunk_id_fn,
        min_count=agg_min_count,
    )
    ordinal = _emit_agg_cards(
        chunks,
        ordinal,
        by_formula,
        cols,
        kind="方名",
        doc_id=doc_id,
        source_file=source_file,
        chunk_id_fn=chunk_id_fn,
        min_count=agg_min_count,
    )
    report["agg_cards"] = len(chunks) - (1 + sum(1 for c in chunks if c.doc_type == "case_row"))
    report["serials"] = len(serial_list)
    return chunks, report


def _split_row_text(
    body: str, cols: list[str], cells: list[str], serial: str, max_len: int
) -> list[str]:
    if len(body) <= max_len:
        return [body]

    head = f"【医案#{serial}】"
    parts: list[str] = []
    cur = [head]
    cur_len = len(head)
    for col, val in _non_empty_pairs(cols, cells):
        if col == "序号":
            continue
        piece = f"{col}: {_clean_cell(val, 400)}"
        if cur and cur_len + len(piece) > max_len:
            parts.append("\n".join(cur))
            cur, cur_len = [head], len(head)
        cur.append(piece)
        cur_len += len(piece)
    if cur:
        parts.append("\n".join(cur))
    return parts


def _emit_agg_cards(
    chunks: list[Chunk],
    ordinal: int,
    groups: dict[str, list[tuple[str, list[str]]]],
    cols: list[str],
    *,
    kind: str,
    doc_id: str,
    source_file: str,
    chunk_id_fn: Any,
    min_count: int,
) -> int:
    diag_idx = cols.index("诊断") if "诊断" in cols else None
    fang_idx = cols.index("方剂") if "方剂" in cols else None

    for key, items in groups.items():
        if len(items) < min_count:
            continue

        formula_counter: Counter[str] = Counter()
        disease_counter: Counter[str] = Counter()
        symptom_bits: Counter[str] = Counter()

        for _serial, cells in items:
            if fang_idx is not None and fang_idx < len(cells):
                txt = cells[fang_idx]
                for f in _FORMULA_TOKEN.findall(txt):
                    formula_counter[f] += 1
            if diag_idx is not None and diag_idx < len(cells):
                disease_counter[cells[diag_idx].strip()] += 1
            # 病机列做症状近似
            for i, c in enumerate(cols):
                if c == "病机" and i < len(cells):
                    for seg in re.split(r"[，,、。；;]", cells[i]):
                        seg = seg.strip()
                        if 2 <= len(seg) <= 8:
                            symptom_bits[seg] += 1

        top_formulas = "、".join(f"{k} {v}" for k, v in formula_counter.most_common(8)) or "未标注"
        top_symptoms = "、".join(k for k, _ in symptom_bits.most_common(8)) or "未标注"
        samples = " ".join(f"#{s}" for s, _ in items[:5])

        if kind == "病名":
            text = (
                f"【医案聚合｜病名:{key}｜共 {len(items)} 例】\n"
                f"常用方（出现次数）: {top_formulas}\n"
                f"常见病机/症状: {top_symptoms}\n"
                f"代表案例: {samples}"
            )
            path = ["医案聚合", f"病名:{key}"]
        else:
            text = (
                f"【医案聚合｜方名:{key}｜共 {len(items)} 例】\n"
                f"涉及病名（出现次数）: {top_formulas and '、'.join(f'{k} {v}' for k, v in disease_counter.most_common(8)) or '未标注'}\n"
                f"常见病机/症状: {top_symptoms}\n"
                f"代表案例: {samples}"
            )
            path = ["医案聚合", f"方名:{key}"]

        ch = Chunk(
            chunk_id=chunk_id_fn(doc_id, ordinal),
            doc_id=doc_id,
            source_file=source_file,
            doc_type="case_agg",
            source_priority=4,
            text=text,
            section_path=path,
            heading_level=2,
            ordinal=ordinal,
        )
        chunks.append(ch.finalize())
        ordinal += 1

    return ordinal


_FORMULA_TOKEN = re.compile(r"[\u4e00-\u9fa5]{2,10}(?:汤|散|丸|膏|丹|饮|煎)")

# 诊断列的占位值，不是真病名
_BAD_DISEASE = {
    "同前", "同上", "同", "无", "-", "---", "未提及", "未明", "不详", "未知", "待查",
    "不詳", "同上。", "同前。", "自查", "其他",
}


def _clean_disease(diag: str) -> str:
    d = re.sub(r"[（(].*?[）)]", "", diag).strip()
    d = d.strip("。.、，, ")
    return d
