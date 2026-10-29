"""从 entries.jsonl 生成判语语料（judges 组）。

比直接用仓库里的 corpus-rag.md 多了两件事：
1. 应用勘误账本（corrections-ledger.jsonl）里已登记的 OCR 错字修正；
2. 分组键用 (book, h2, section)，保证「同一条文讲评」聚成一段。

输出格式与解析器对齐：每行一句判语，句末带 [BVID#ep §section @时间] 出处标记。
"""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JUDGE_DIR = ROOT / "kb_src" / "external" / "nihai-judges"


def load_corrections() -> dict[tuple[str, str, str], tuple[str, str]]:
    """(bvid, ep, t_hms) → (original, suggested)"""
    path = JUDGE_DIR / "corrections-ledger.jsonl"
    out: dict[tuple[str, str, str], tuple[str, str]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        orig = (r.get("original") or "").strip()
        sug = (r.get("suggested") or "").strip()
        if not orig or not sug or orig == sug:
            continue
        key = (str(r.get("bvid")), str(r.get("ep")), str(r.get("t_hms")))
        out[key] = (orig, sug)
    return out


def main() -> None:
    entries_path = JUDGE_DIR / "entries.jsonl"
    if not entries_path.is_file():
        print("✗ 缺少 entries.jsonl，先跑 fetch_external_kb")
        return

    corrections = load_corrections()
    print(f"勘误条目: {len(corrections)}")

    rows: list[dict] = []
    for line in entries_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    print(f"判语条目: {len(rows)}")

    fixed = 0
    for r in rows:
        key = (str(r.get("bvid")), str(r.get("ep")), str(r.get("t_hms")))
        c = corrections.get(key)
        if c:
            orig, sug = c
            q = str(r.get("quote") or "")
            if orig in q:
                r["quote"] = q.replace(orig, sug)
                fixed += 1
    print(f"已修正错字: {fixed} 处")

    # 按 (book, h2, section) 聚合成段
    groups: "OrderedDict[tuple[str, str, str], list[dict]]" = OrderedDict()
    for r in rows:
        key = (str(r.get("part_name") or r.get("book")), str(r.get("h2") or ""), str(r.get("section") or ""))
        groups.setdefault(key, []).append(r)

    lines: list[str] = [
        "# 倪海厦《人纪》判语 · RAG 整包",
        "",
        "> 由 entries.jsonl 生成，已应用勘误账本中的 OCR 错字修正。",
        "> 每段末尾列出该段全部出处，可回放核对。",
        "",
    ]
    n_quotes = 0
    for (part, h2, _section), items in groups.items():
        title = " · ".join(x for x in (part, h2, items[0].get("h3") or "") if x)
        lines.append(f"## {title}")
        lines.append("")
        for r in items:
            q = str(r.get("quote") or "").strip()
            if not q:
                continue
            cite = f"[{r.get('bvid')}#{r.get('ep')} §{r.get('section')} @{r.get('t_hms')}]"
            lines.append(f"{q}　{cite}")
            n_quotes += 1
        lines.append("")

    out = JUDGE_DIR / "corpus-rag.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"段落: {len(groups)}  判语: {n_quotes}")
    print(f"输出: {out}  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
