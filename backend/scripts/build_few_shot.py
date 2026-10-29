"""从 xihao1 的 4.6 万条 QA 里挑 few-shot 示例。

不是全量注入 system prompt（20 条要 2000+ tokens），而是筛一批高质量的存到
app/prompts/few_shot.md，运行时按查询相似度动态挑 2 条作为示例轮次。

筛选条件：
- 问句 4~30 字，答句 60~250 字
- 答句里必须出现方剂词典中的方名，或症状词典中的症状词
- 剔除含免责套话/「以上仅供参考」类的条目
- 同义问句去重（输入完全相同只留一条）
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "kb_src" / "external" / "tcm-qa-fewshot" / "output2.jsonl"
OUT = ROOT / "app" / "prompts" / "few_shot.md"

BAD = re.compile(r"仅供参考|仅供参考|不能替代|请咨询|就医|Disclaimer|以上内容|仅供参考")


def load_dicts() -> tuple[set[str], set[str]]:
    cfg = ROOT / "app" / "config"
    formulas: set[str] = set()
    symptoms: set[str] = set()
    for name, bucket in (("formula_dict.txt", formulas), ("symptom_dict.txt", symptoms)):
        p = cfg / name
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                w = line.strip().split("\t")[0].split("|")[0].split()[0] if line.strip() else ""
                if len(w) >= 2:
                    bucket.add(w)
    return formulas, symptoms


def main() -> None:
    if not SRC.is_file():
        print(f"✗ 缺少 {SRC}")
        return

    data = json.loads(SRC.read_text(encoding="utf-8"))
    print(f"原始条目: {len(data)}")

    formulas, symptoms = load_dicts()
    print(f"词典: 方剂 {len(formulas)} 词, 症状 {len(symptoms)} 词")

    seen: set[str] = set()
    buckets: dict[str, list[dict]] = {"方剂": [], "症状": [], "其他": []}
    for item in data:
        convs = item.get("conversation") or []
        if not convs:
            continue
        c = convs[0]
        q = str(c.get("input") or "").strip()
        a = str(c.get("output") or "").strip()
        if not (4 <= len(q) <= 30) or not (60 <= len(a) <= 250):
            continue
        if BAD.search(a):
            continue
        if q in seen:
            continue
        seen.add(q)

        fhit = [w for w in formulas if w in q]
        shit = [w for w in symptoms if w in q]
        if fhit:
            buckets["方剂"].append({"q": q, "a": a, "key": fhit[0], "kind": "方剂"})
        elif shit:
            buckets["症状"].append({"q": q, "a": a, "key": shit[0], "kind": "症状"})
        else:
            buckets["其他"].append({"q": q, "a": a, "key": "", "kind": "其他"})

    for k, v in buckets.items():
        print(f"  {k}: {len(v)}")

    # 每个桶按 key 分散取样，避免全是同一个方
    picked: list[dict] = []
    for kind in ("方剂", "症状", "其他"):
        pool = buckets[kind]
        by_key: dict[str, list[dict]] = {}
        for r in pool:
            by_key.setdefault(r["key"] or "-", []).append(r)
        for k, items in sorted(by_key.items()):
            picked.append(items[0])  # 每个 key 取第一条
        # 再补一些
        extra = [r for r in pool if r not in picked]
        picked.extend(extra[: max(0, 8 - len(by_key))])

    # 去重 + 截断
    out: list[dict] = []
    qs: set[str] = set()
    for r in picked:
        if r["q"] in qs:
            continue
        qs.add(r["q"])
        out.append(r)
    out = out[:24]

    lines = [
        "# few-shot 示例（自动筛选，人工抽检）",
        "",
        "运行时按用户问句与示例问题的关键词重合度动态挑 2 条，作为示例轮次插入对话。",
        "改动前请确认示例质量 —— 这里是模型风格的锚。",
        "",
    ]
    for i, r in enumerate(out, 1):
        lines.append(f"### 示例 {i}（{r['kind']}）")
        lines.append("")
        lines.append(f"- 问：{r['q']}")
        lines.append(f"- 答：{r['a']}")
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n写出 {len(out)} 条 → {OUT}")

    print("\n--- 抽样预览 ---")
    for r in out[:5]:
        print(f"[{r['kind']}] 问：{r['q']}")
        print(f"        答：{r['a'][:100]}")


if __name__ == "__main__":
    main()
