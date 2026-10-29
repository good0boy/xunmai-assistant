"""知识库来源分组。

三个**独立集合**，各自建库、各自调权、各自溯源：

- ``nihai``    倪师解读（主）。原始的 43 个 Markdown。
- ``classics`` 经典原文（辅）。《本草纲目》《千金要方》《针灸甲乙经》等 21 本。
- ``judges``   判语（最低优先级）。倪师原话字幕级引文，每条带 B 站视频号与时间戳。

同源判定用 ``origin``：同一典籍的「倪师解读」与「经典原文」算同源，
检索合并时倪师组优先，经典原文只占补位名额。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

NIHAI_GROUP = "nihai"
CLASSICS_GROUP = "classics"
JUDGES_GROUP = "judges"

# 中文关键词 → origin slug。按出现顺序匹配，长的优先。
ORIGIN_KEYS: list[tuple[str, str]] = [
    ("针灸甲乙经", "jiayi"),
    ("甲乙经", "jiayi"),
    ("黄帝八十一难经", "nanjing"),
    ("八十一难经", "nanjing"),
    ("难经", "nanjing"),
    ("千金翼方", "qianjin_yifang"),
    ("千金要方", "qianjin_yaofang"),
    ("千金要方", "qianjin_yaofang"),
    ("肘后备急方", "zhouhou_beijifang"),
    ("本草纲目", "bencao_gangmu"),
    ("神农本草经", "shennong"),
    ("本草经", "shennong"),
    ("金匮要略", "jinkui"),
    ("金匮", "jinkui"),
    ("伤寒论", "shanghan_lun"),
    ("伤寒", "shanghan_lun"),
    ("黄帝内经", "neijing"),
    ("灵枢", "neijing"),
    ("素问", "neijing"),
    ("内经", "neijing"),
    ("中医养生学", "yangsheng"),
    ("中医眼科备读", "yanke"),
    ("中医伤科按摩学", "shangke"),
    ("中药基本理论知识", "zhongyao_jichu"),
    ("邹孟城", "zoumengcheng"),
    ("针灸", "zhenjiu"),
    ("腧穴", "zhenjiu"),
    ("经络", "jingluo"),
    ("穴位", "zhenjiu"),
]

# 判语出处：[BV1rL8xzAEWA#15 §1 @00:00:27]
JUDGE_CITE_RE = re.compile(
    r"\[(?P<bvid>BV[0-9A-Za-z]+)#(?P<ep>\d+)\s*§(?P<section>\d+)\s*@(?P<t>\d{2}:\d{2}:\d{2})\]"
)


# 拼音文件名 → origin。讲义文件名是人标的，比文本推断可靠：
# modules/01_shanghan_sun.md 的正文几乎不出现「伤寒论」三个字（都在讲条文），
# 文本推断对这 69% 的文件全部失灵。
# 只映射「整个文件主题明确属于一部典籍」的；混合主题（针灸本草、仲景心法、
# 经方、医案）不映射 —— 宁缺毋滥，错标 origin 会破坏同源让位。
FILE_ORIGIN_KEYS: list[tuple[str, str]] = [
    ("shanghan", "shanghan_lun"),  # 01_shanghan_sun / 02_shanghan_other / 13_shanghan_quebing
    ("jingui", "jinkui"),          # 04_jingui
    ("huangdi", "neijing"),        # 05_huangdi_neijing / 08_huangdi_detail
]


def detect_origin(text: str, source_file: str = "") -> str:
    """判定内容属于哪部典籍。用于同源去重（倪师解读优先于经典原文）。

    文件名映射优先于文本推断：文件名是人工标的，文本里的「伤寒」可能只是顺带一提。
    """
    fname = source_file.lower()
    for key, slug in FILE_ORIGIN_KEYS:
        if key in fname:
            return slug
    hay = f"{source_file}\n{text[:4000]}"
    for key, slug in ORIGIN_KEYS:
        if key in hay:
            return slug
    return ""


# 条文/篇号：倪师块写「条文181（一百八十一）」，判语段写「伤寒论 · 条225」
ARTICLE_RE = re.compile(r"(?:条文|条)\s*(\d{1,4})")
CHAPTER_RE = re.compile(r"篇\s*(\d{1,4})")


def extract_article_no(section_path: str, text: str = "") -> str:
    """提取条文号 / 篇号。

    优先看**正文**：倪师块正文里有「#### 条文105：小柴胡汤证」这种精确标题；
    章节路径是拼接的（「太阳病篇（条文1-129）> … > 条文96-114」），
    只能给出范围，取出来不精确。

    用于把「倪师解读的某一条」和「判语里的某一段」对上 ——
    这是出处能否真正可核对的关键：dense 分数接近只能说明「讲同一类东西」，
    条文号相同才说明「讲的是同一条」。
    """
    for src in (text[:120] if text else "", section_path):
        if not src:
            continue
        found = ARTICLE_RE.findall(src)
        if found:
            return found[-1]
        found = CHAPTER_RE.findall(src)
        if found:
            return found[-1]
    return ""


def collection_for_group(group: str | None) -> str:
    """来源分组 → 集合名。

    邻块展开、chunk 详情这些「按 chunk_id 反查」的场景必须知道去哪个集合找 ——
    之前一律查 tcm_kb，导致经典原文与判语的引用永远查不到原文出处。
    """
    from app.core.config import settings

    if group == CLASSICS_GROUP:
        return settings.qdrant_collection_classics
    if group == JUDGES_GROUP:
        return settings.qdrant_collection_judges
    return settings.qdrant_collection


def parse_judge_cites(text: str) -> list[dict[str, str]]:
    """从判语正文里解析出处标记。"""
    out: list[dict[str, str]] = []
    for m in JUDGE_CITE_RE.finditer(text):
        t = m.group("t")
        h, mi, s = t.split(":")
        sec = int(h) * 3600 + int(mi) * 60 + int(s)
        bvid = m.group("bvid")
        out.append(
            {
                "bvid": bvid,
                "ep": m.group("ep"),
                "section": m.group("section"),
                "t_hms": t,
                "start_sec": str(sec),
                "url": f"https://www.bilibili.com/video/{bvid}?t={sec}",
            }
        )
    return out


@dataclass
class ExternalSource:
    """一组外部文件，对应一个集合。"""

    group: str
    collection: str
    root: Path
    doc_type: str
    priority: int
    source_prefix: str
    origin_by_file: dict[str, str] = field(default_factory=dict)
    kind: str = "markdown"  # markdown | judges
