"""输出后置校验：八查。

模型不听话是常态。靠 prompt 保证格式不可靠，必须在服务端做确定性检查，
不通过就带着具体问题再请求一次（只修格式，不重写内容）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.prompts.assembler import ENDINGS

# 口头禅 / 追问 的检测词
CATCHPHRASES = ["对不对", "我跟你说", "你看啊", "你看", "嚎", "我问你", "你想想看"]
QUESTIONS = ["我问你", "你想想看", "懂了没有", "记不记得", "你有没有"]

DANGUE_RE = re.compile(r"\*\*辨证[：:]\s*")
TABLE_RE = re.compile(r"^\s*\|.+\|\s*$", re.MULTILINE)
BOLD_RE = re.compile(r"\*\*[^*\n]+\*\*")
CARD_RE = re.compile(r"📜")

# 路径泄漏
LEAK_RES = [
    re.compile(r"modules/\d"),
    re.compile(r"references/"),
    re.compile(r"cases/\d"),
    re.compile(r"\.md\b"),
    re.compile(r"第\s*\d+\s*行"),
    re.compile(r"SKILL\.md"),
]

FORMULA_RE = re.compile(r"[\u4e00-\u9fa5]{2,8}(?:汤|散|丸|膏|丹)(?:主之)?")


@dataclass
class ValidationResult:
    issues: list[str] = field(default_factory=list)
    hard_fail: bool = False
    corrected: str = ""

    @property
    def ok(self) -> bool:
        return not self.issues


def _check_citations(text: str, n_citations: int) -> list[str]:
    issues: list[str] = []
    found = [int(x) for x in re.findall(r"【(\d{1,2})】", text)]

    if n_citations > 0 and not found:
        # 有证据却一个角标都不标，引用就形同虚设，用户无法溯源
        issues.append(f"正文没有引用角标：手上有 {n_citations} 条证据，至少应标注 1 处")

    over = [m for m in found if m > n_citations and n_citations > 0]
    if over:
        issues.append(f"角标越界：正文引用了不存在的【{over[0]}】，共 {n_citations} 条证据")
    return issues


def _check_leak(text: str) -> list[str]:
    for rx in LEAK_RES:
        m = rx.search(text)
        if m:
            return [f"输出了知识库路径/行号（{m.group(0)}），必须删除"]
    return []


def _check_endings(text: str, used: list[str]) -> tuple[list[str], str | None]:
    """结尾必须是断言式，且不能重复本会话已用过的。"""
    tail = text.strip()[-120:]
    hit = next((e for e in ENDINGS if e in tail), None)
    if hit is None:
        return ["结尾没有断言式收束（应从结尾短语池里挑一个放在最后）"], None
    if hit in used:
        return [f"结尾短语「{hit}」本会话已用过，必须换一个"], None
    return [], hit


def _has_bold_highlight(text: str) -> bool:
    """「加粗要点」的判据要排除辨证行。

    否则一篇只写了辨证行、没有真正重点标注的回答也能蒙混过关 ——
    这恰恰是原 skill 明确禁止的"纯学术骨架"。
    """
    for m in BOLD_RE.finditer(text):
        inner = m.group(0).strip("*").strip()
        if not inner or len(inner) < 3:
            continue
        if inner.startswith("辨证") or "辨证：" in inner or "辨证:" in inner:
            continue
        # 表格单元格里的加粗不算独立要点块
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        line = text[line_start : line_end if line_end > 0 else len(text)]
        if line.strip().startswith("|"):
            continue
        return True
    return False


def validate(
    text: str,
    *,
    n_citations: int,
    used_endings: list[str],
    require_formula: bool = True,
    red_flag: bool = False,
) -> ValidationResult:
    res = ValidationResult()
    text = text.strip()
    if not text:
        res.issues.append("输出为空")
        res.hard_fail = True
        return res

    has_table = bool(TABLE_RE.search(text))
    has_bold = _has_bold_highlight(text)
    if not (has_table or has_bold):
        res.issues.append("缺重点标注：至少要有 1 个表格或加粗要点块")

    # 红旗症状场景下不要求辨证行（先救人）
    if not red_flag and not DANGUE_RE.search(text):
        res.issues.append("缺辨证行，格式应为 **辨证：证型（病机·方名证）**")

    catches = sum(1 for c in CATCHPHRASES if c in text)
    if catches < 2:
        res.issues.append(f"口头禅不足（当前 {catches} 处，需 ≥2）")

    if not any(q in text for q in QUESTIONS):
        res.issues.append("缺上课式追问（用'我问你/你想想看/懂了没有'）")

    end_issues, _ending = _check_endings(text, used_endings)
    res.issues.extend(end_issues)

    res.issues.extend(_check_leak(text))
    res.issues.extend(_check_citations(text, n_citations))

    # 方剂名出现但完全没有条文卡片
    if require_formula and not red_flag:
        formulas = set(FORMULA_RE.findall(text))
        if formulas and not CARD_RE.search(text):
            sample = next(iter(formulas))
            res.issues.append(f"提到了方剂（如{sample}）但没有出经方条文卡片")

    # 硬失败：路径泄漏或格式骨架完全缺失
    if any("路径" in i or "行号" in i for i in res.issues):
        res.hard_fail = True

    return res


def strip_bad_citations(text: str, n_citations: int) -> str:
    """直接动手：剥掉越界角标。这比让模型改更可靠。"""

    def repl(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return "" if n > n_citations or n < 1 else m.group(0)

    return re.sub(r"【(\d{1,2})】", repl, text)


def strip_leaks(text: str) -> str:
    """把泄漏的路径片段替换成中性说法。"""
    text = re.sub(r"`?modules/\d+[\w_/.\-]*`?", "相关篇章", text)
    text = re.sub(r"`?references/[\w_/.\-]*`?", "速查资料", text)
    text = re.sub(r"`?cases/\d+[\w_./\-]*`?", "医案记录", text)
    text = re.sub(r"`?SKILL\.md`?", "经方速查", text)
    text = re.sub(r"第\s*\d+\s*行", "原书相应位置", text)
    return text


def build_fix_message(issues: list[str]) -> str:
    lines = ["你的上一个回答有以下格式问题，请只修正格式，不要改写内容、不要重新论证："]
    lines.extend(f"- {x}" for x in issues)
    lines.append("")
    lines.append("修正后直接输出完整回答（保留全部原有内容，只补格式）。")
    return "\n".join(lines)
