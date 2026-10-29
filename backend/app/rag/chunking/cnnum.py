"""中文数字转换。用于解析「金匮要略·第X篇」这类篇号。"""

from __future__ import annotations

import re

_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def cn_to_int(s: str) -> int | None:
    """把「八」「二十三」「十」这类中文数字转成整数。"""
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)

    if "十" in s:
        head, _, tail = s.partition("十")
        tens = _DIGITS.get(head, 1) if head else 1
        ones = _DIGITS.get(tail, 0) if tail else 0
        if head and head not in _DIGITS:
            return None
        if tail and tail not in _DIGITS:
            return None
        return tens * 10 + ones

    if len(s) == 1:
        return _DIGITS.get(s)
    return None


_CHAPTER_RES = [
    # 「第二十三篇」「第二章」这类显式写法
    re.compile(r"第([零〇一二两三四五六七八九十]+)(?:篇|章)"),
    # 金匮篇名惯例：以「第八」「第一」结尾，没有「篇」字
    # 例：奔豚气病脉证并治第八 / 藏府经络先后病脉证第一 / 惊悸吐衄下血胸满瘀血病脉证第十六
    re.compile(r"第([零〇一二两三四五六七八九十]+)\s*$"),
]


def extract_chapter_no(title: str) -> int | None:
    t = title.strip().strip("（）()【】[] ")
    for rx in _CHAPTER_RES:
        m = rx.search(t)
        if m:
            no = cn_to_int(m.group(1))
            if no and 1 <= no <= 30:
                return no
    return None
