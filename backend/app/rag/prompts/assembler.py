"""Prompt 五层组装。

L0 角色与安全 / L1 风格卡 / L2 引用格式 —— 固定层，吃前缀缓存
L3 会话摘要 / L4 历史滑窗 / L5 检索证据 —— 动态层
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Template

from app.core.config import settings
from app.rag.retrieve.hybrid import Hit

logger = logging.getLogger(__name__)

# app/rag/prompts/assembler.py → parents[0]=prompts, [1]=rag, [2]=app
PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"

# few-shot 示例：按问句关键词重合度动态挑 2 条插入，作为格式与风格的锚。
# 不做全量注入（示例集全塞进 system prompt 要 2000+ tokens）。
FEW_SHOT_FILE = PROMPT_DIR / "few_shot.md"
FEW_SHOT_COUNT = 2


@lru_cache(maxsize=1)
def load_few_shot() -> list[dict[str, str]]:
    """解析 few_shot.md → [{"q": ..., "a": ..., "keys": [...]}]"""
    if not FEW_SHOT_FILE.is_file():
        return []
    out: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    mode: str | None = None
    for raw in FEW_SHOT_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.rstrip()
        if line.startswith("### 问"):
            cur = {"q": "", "a": ""}
            mode = "q"
            continue
        if line.startswith("### 答"):
            mode = "a"
            if cur.get("q"):
                out.append(cur)
            continue
        if line.startswith("## ") and not line.startswith("### "):
            mode = None
            continue
        if mode and cur:
            if line.strip():
                cur[mode] = (cur[mode] + "\n" + line).strip() if cur[mode] else line
    # 关键词：取问句里的中文词（2 字以上）
    for item in out:
        item["keys"] = "".join(
            ch for ch in item["q"] if "一" <= ch <= "鿿"
        )
    return out


def pick_few_shot(query: str, count: int = FEW_SHOT_COUNT) -> list[dict[str, str]]:
    """挑与当前问句关键词重合度最高的 few-shot 示例。"""
    shots = load_few_shot()
    if not shots:
        return []
    q = "".join(ch for ch in query if "一" <= ch <= "鿿")
    if not q:
        return shots[:count]
    scored = []
    for s in shots:
        keys = s.get("keys") or ""
        overlap = sum(1 for i in range(len(q) - 1) if q[i : i + 2] in keys)
        scored.append((overlap, s))
    scored.sort(key=lambda x: -x[0])
    picked = [s for score, s in scored[:count] if score > 0]
    if not picked:
        picked = [s for _, s in scored[:count]]
    return picked


# 给模型看的来源名，不暴露文件路径
SOURCE_LABELS: dict[str, str] = {
    "modules/01_shanghan_sun.md": "伤寒论·太阳病篇",
    "modules/02_shanghan_other.md": "伤寒论·阳明少阳太阴少阴厥阴",
    "modules/03_yian.md": "倪师医案集",
    "modules/04_jingui.md": "金匮要略",
    "modules/05_huangdi_neijing.md": "黄帝内经",
    "modules/06_liangdong.md": "梁冬对话倪海厦",
    "modules/07_bimen_hantang.md": "人纪闭门课与汉唐文章",
    "modules/08_huangdi_detail.md": "黄帝内经讲义详解",
    "modules/09_zhenjiu_bencao.md": "针灸教程与神农本草经",
    "modules/10_fuyang_luntan.md": "扶阳论坛演讲",
    "modules/11_zhongjing_xinfa.md": "仲景心法",
    "modules/12_stanford_jingfang.md": "斯坦福演讲与经方妙用",
    "modules/13_shanghan_quebing.md": "伤寒论·太阳下篇与阳明篇",
    "modules/14_yijinjing_bidu.md": "易筋经与五脏逼毒法",
    "cases/00_merged_table.md": "倪师医案总表",
    "cases/01_cancer.md": "癌症医案",
    "cases/02_cardiovascular.md": "心血管医案",
    "cases/03_metabolic.md": "代谢病医案",
    "cases/04_autoimmune.md": "自身免疫医案",
    "cases/05_neurological.md": "神经精神医案",
    "cases/06_other.md": "其他医案",
    "distilled_cases.md": "医案叙事清单",
    "SKILL.md": "经方速查",
    "expression_style.md": "倪师表达风格",
    "references/distilled/01-six-meridian-formulas.md": "六经辨证诊断公式",
    "references/distilled/02-acupuncture-quick-ref.md": "针灸公式速查",
    "references/distilled/03-clinical-experience.md": "临床经验·感冒六方",
    "references/distilled/04-acupuncture-highlights.md": "针灸要点汇编",
    "references/distilled/05-clinical-dose-quickref.md": "经方临床剂量速查",
    "references/distilled/06-clinical-dose-c99.md": "经方临床剂量速查（C类）",
}

ENDINGS = [
    "就这么简单", "断根", "一剂就好", "不用怕", "记牢了", "包好",
    "这是铁律", "一通百通", "医圣早就讲清楚了", "别搞反了", "你自己想",
    "临床上常看到", "不会错", "这才叫治病", "一清二楚", "放胆用",
    "别乱来", "道理就在这",
]


@lru_cache(maxsize=32)
def _read(name: str) -> str:
    path = PROMPT_DIR / name
    if not path.exists():
        logger.warning("prompt 文件缺失: %s", name)
        return ""
    return path.read_text(encoding="utf-8")


def source_label(hit: Hit) -> str:
    src = str(hit.payload.get("source_file", ""))
    base = SOURCE_LABELS.get(src)
    if base is None:
        base = src.split("/")[-1].replace(".md", "") or "知识库"

    # 章节取最后两级，避免过长
    path = hit.payload.get("section_path") or []
    tail = [p for p in path[-2:] if p and p != base]
    doc_id = str(hit.payload.get("doc_id", ""))[:6]

    bits = [base]
    if tail:
        bits.append("·".join(tail))
    if hit.payload.get("article_no"):
        bits.append(f"条文{hit.payload['article_no']}")
    if hit.payload.get("case_serial"):
        bits.append(f"医案#{hit.payload['case_serial']}")
    return f"［{doc_id}］" + " ｜ ".join(bits)


def render_evidence(hits: list[Hit], *, insufficient: bool = False) -> str:
    tpl = Template(_read("evidence.md.j2"))
    items = [
        {"label": source_label(h), "text": h.text.strip()} for h in hits
    ]
    return tpl.render(hits=items, insufficient=insufficient).strip()


def render_uncovered() -> str:
    return Template(_read("uncovered.md.j2")).render().strip()


@dataclass
class AssembledPrompt:
    messages: list[dict[str, Any]]
    citations: list[dict[str, Any]] = field(default_factory=list)
    evidence_chars: int = 0
    system_chars: int = 0

    @property
    def estimated_input_tokens(self) -> int:
        total = self.system_chars + sum(
            len(str(m.get("content", ""))) for m in self.messages if m["role"] != "system"
        )
        return int(total * 0.7)


def build_system_prompt(used_endings: list[str]) -> str:
    l0 = _read("l0_system.md").strip()
    l1 = Template(_read("l1_style_card.md")).render(used_endings="、".join(used_endings))
    l2 = _read("l2_citation.md").strip()
    return f"{l0}\n\n---\n\n{l1}\n\n---\n\n{l2}"


def build_messages(
    *,
    query: str,
    hits: list[Hit],
    history: list[dict[str, str]] | None = None,
    summary: str | None = None,
    used_endings: list[str] | None = None,
    vision_block: str | None = None,
    uncovered: bool = False,
    insufficient: bool = False,
) -> AssembledPrompt:
    system = build_system_prompt(used_endings or [])
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

    # L3 会话摘要
    if summary:
        messages.append(
            {
                "role": "system",
                "content": f"【本次会话已讨论的内容摘要】\n{summary}",
            }
        )

    # L4 历史滑窗
    if history:
        messages.extend(_trim_history(history))

    # few-shot 示例轮次：按问句相似度动态挑 2 条，作为格式与语气的锚
    for shot in pick_few_shot(query):
        messages.append({"role": "user", "content": f"【示例问题】\n{shot['q']}"})
        messages.append({"role": "assistant", "content": shot["a"]})

    # L5 证据 + 用户消息，紧贴末位
    blocks: list[str] = []
    if vision_block:
        blocks.append(vision_block.strip())

    if uncovered:
        blocks.append(render_uncovered())
    elif hits:
        blocks.append(render_evidence(hits, insufficient=insufficient))
    else:
        blocks.append(render_uncovered())

    blocks.append(f"【用户问题】\n{query}")
    messages.append({"role": "user", "content": "\n\n".join(blocks)})

    citations = [
        {
            "n": i + 1,
            "chunk_id": h.chunk_id,
            "doc_id": h.payload.get("doc_id"),
            "source_file": h.payload.get("source_file"),
            "section_path": h.section_path_str,
            "label": source_label(h),
            "snippet": h.text[:400],
            "score": round(h.rrf_score, 4),
            "rank": i + 1,
            # 来源分组：nihai 倪师解读 / classics 经典原文
            "source_group": h.payload.get("source_group", "nihai"),
            "origin": h.payload.get("origin", ""),
            # 判语出处（B 站视频号 + 时间戳），仅倪师解读命中且能对上判语时有值
            "judge_cites": h.payload.get("judge_cites") or [],
        }
        for i, h in enumerate(hits)
    ]

    return AssembledPrompt(
        messages=messages,
        citations=citations,
        evidence_chars=sum(len(h.text) for h in hits),
        system_chars=len(system),
    )


def _trim_history(history: list[dict[str, str]]) -> list[dict[str, Any]]:
    """历史注入：按 token 预算**从最新往回**装，条数是硬上限。

    history_token_budget 之前只是个摆设（定义了但从没用）——只按
    history_max_turns 截条数，10 轮之前的对话内容直接丢，用户感知就是
    「聊着聊着它忘了」。现在按预算从最新往回收：中文 1 token ≈ 1.6 字符，
    budget 2000 ≈ 3200 字符的窗口。assistant 超长消息截断为中段省略，
    避免单条长回复吃光预算。
    """
    max_msgs = settings.history_max_turns * 2
    budget_chars = int(settings.history_token_budget * 1.6)

    out: list[dict[str, Any]] = []
    used = 0
    # 从最新往回收集，装满预算为止；最后反转回时间正序
    for m in reversed(history[-max_msgs:]):
        content = str(m.get("content", ""))
        if m.get("role") == "assistant" and len(content) > 400:
            content = f"{content[:200]}\n……（中间省略）……\n{content[-150:]}"
        cost = len(content)
        if out and used + cost > budget_chars:
            break  # 这条装不下（最新的一条除外——至少保最近一轮）
        out.append({"role": m.get("role", "user"), "content": content})
        used += cost

    out.reverse()
    return out


def pick_ending_hint(used: list[str]) -> str:
    """给校验器用：返回一个尚未使用的结尾短语。"""
    for e in ENDINGS:
        if e not in used:
            return e
    return ENDINGS[0]
