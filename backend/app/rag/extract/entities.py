"""实体抽取。全部走确定性规则：词典最长匹配 + 正则兜底。不用 LLM。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.extract.dicts import Dicts

ARTICLE_RES = [
    re.compile(r"第\s*(\d{1,4})\s*条"),
    re.compile(r"条文\s*(\d{1,4})"),
]

# 正则兜底：XX汤 / XX散 / XX丸 …，要求后接方剂上下文词，否则不认
FORMULA_RE = re.compile(r"[\u4e00-\u9fa5]{2,10}(?:汤|散|丸|膏|丹|饮|煎)")
FORMULA_CONTEXT = re.compile(r"(?:主之|与之|宜|服|方|证|加|减|倍)")

# 正则从句子开头贪婪吞字（"更多的滋阴汤""我给他开桂枝汤"），必须从左裁掉这些前缀
_FORMULA_LEAD_JUNK = (
    "更多的", "很多的", "甚多的", "许多的", "很多的", "大多数的", "一样的", "一个", "一些",
    "这种", "那种", "什么", "如果", "因为", "所以",
    "但是", "而且", "还有", "不是", "就是", "也是", "好像", "比如", "例如", "这个", "那个",
    "我们", "你们", "他们", "我", "你", "他", "她", "它", "给", "用", "吃", "喝", "开",
    "加", "是", "的", "了", "在", "和", "与", "也", "又", "就", "都", "还", "把", "被",
    "让", "说", "讲", "问", "像", "比较", "再", "先", "后", "能", "会", "要", "想", "煮",
    "煎", "服", "用这", "这", "那", "个", "碗", "两", "三", "四", "五",
)


def _trim_formula(word: str) -> str:
    """从左边反复裁掉虚词/动词，直到剩下真正的方名。"""
    changed = True
    while changed:
        changed = False
        for junk in _FORMULA_LEAD_JUNK:
            if word.startswith(junk) and len(word) - len(junk) >= 2:
                word = word[len(junk) :]
                changed = True
                break
    return word
ACUPOINT_SUFFIX_RE = re.compile(r"[\u4e00-\u9fa5]{2,3}(?:穴|俞|募)")

# 明显不是方名的后缀组合（如"这汤""一碗汤"）
_BAD_FORMULA_PREFIX = ("这", "那", "碗", "个", "一", "两", "三", "四", "五", "六", "七", "八", "九", "十")
_BAD_FORMULA_EXACT = {"什么汤", "这个汤", "那个汤", "一碗汤", "药汤", "汤", "散", "丸"}


@dataclass
class Extracted:
    formulas: list[str] = field(default_factory=list)
    acupoints: list[str] = field(default_factory=list)
    diseases: list[str] = field(default_factory=list)
    symptoms: list[str] = field(default_factory=list)
    herbs: list[str] = field(default_factory=list)
    article_nos: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.formulas or self.acupoints or self.diseases or self.symptoms or self.herbs
        )

    def summary(self) -> str:
        return (
            f"方:{'/'.join(self.formulas[:4])} 症:{'/'.join(self.symptoms[:4])} "
            f"病:{'/'.join(self.diseases[:3])} 穴:{'/'.join(self.acupoints[:3])}"
        )


class EntityExtractor:
    def __init__(self, dicts: Dicts) -> None:
        self.d = dicts

    def extract(self, text: str, *, use_regex_fallback: bool = True) -> Extracted:
        formulas = self.d.find_formulas(text)
        if use_regex_fallback:
            for m in FORMULA_RE.finditer(text):
                cand = _trim_formula(m.group(0))
                if (
                    cand in _BAD_FORMULA_EXACT
                    or cand.startswith(_BAD_FORMULA_PREFIX)
                    or cand in formulas
                    or not (3 <= len(cand) <= 8)
                ):
                    continue
                tail = text[m.end() : m.end() + 4]
                if FORMULA_CONTEXT.search(tail) and cand not in formulas:
                    formulas.append(cand)

        acupoints = self.d.find_acupoints(text)
        if use_regex_fallback:
            for m in ACUPOINT_SUFFIX_RE.finditer(text):
                cand = m.group(0)
                if cand not in acupoints and len(cand) >= 3:
                    acupoints.append(cand)

        article_nos: list[str] = []
        for rx in ARTICLE_RES:
            for m in rx.finditer(text):
                no = m.group(1)
                if no not in article_nos:
                    article_nos.append(no)

        return Extracted(
            formulas=_uniq(formulas, 16),
            acupoints=_uniq(acupoints, 12),
            diseases=_uniq(self.d.find_diseases(text), 12),
            symptoms=_uniq(self.d.find_symptoms(text), 12),
            herbs=_uniq(self.d.find_herbs(text), 12),
            article_nos=article_nos[:6],
        )

    # 供 table.py 等回调使用
    def diseases_of(self, text: str) -> list[str]:
        return _uniq(self.d.find_diseases(text), 12)

    def formulas_of(self, text: str) -> list[str]:
        return _uniq(self.d.find_formulas(text), 12)


def _uniq(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for x in items:
        if x and x not in out:
            out.append(x)
        if len(out) >= limit:
            break
    return out
