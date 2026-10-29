"""Markdown 块级解析。

只做一件事：把文本切成有类型的块，供上层按结构分组。
不做语义判断，不做切分决策。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

BlockKind = Literal["heading", "para", "table", "quote", "list", "code", "hr"]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Block:
    kind: BlockKind
    text: str
    level: int = 0  # 仅 heading 有效
    line_no: int = 0
    title: str = ""  # 仅 heading 有效：去掉 # 标记后的标题文本

    @property
    def char_len(self) -> int:
        return len(self.text)

    def is_split_protected(self) -> bool:
        """结构完整性优先的块：表格、引用、代码。这些块不允许在内部切开。"""
        return self.kind in ("table", "quote", "code")


@dataclass
class Section:
    """一个标题及其直接内容（不含子标题内容）。"""

    level: int
    title: str
    path: list[str]
    heading_line: str
    direct_blocks: list[Block] = field(default_factory=list)
    children: list[Section] = field(default_factory=list)

    @property
    def path_str(self) -> str:
        return " > ".join(self.path)

    def direct_len(self) -> int:
        return sum(b.char_len for b in self.direct_blocks)

    def total_len(self) -> int:
        return self.direct_len() + sum(c.total_len() for c in self.children)


def normalize(text: str) -> str:
    """统一换行、去 BOM、去零宽字符、压缩多余空行。"""
    text = text.replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for ch in ("\u200b", "\u200c", "\u200d", "\u3000"):
        text = text.replace(ch, "" if ch != "\u3000" else " ")
    # 三个以上空行压成两个
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def strip_frontmatter(text: str) -> str:
    """去掉开头的 YAML frontmatter。Skill 文件头部的 name/description 不该进正文。"""
    t = text.lstrip("\n")
    if not t.startswith("---"):
        return text
    end = t.find("\n---", 3)
    if end == -1 or end > 3000:
        return text
    return t[end + 4 :].lstrip("\n")


def parse_blocks(text: str) -> list[Block]:
    """按行扫描，产出块列表。表格与引用块连续行合并为一个块。"""
    lines = text.split("\n")
    blocks: list[Block] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 空行
        if not stripped:
            i += 1
            continue

        # 围栏代码块
        m = _FENCE_RE.match(line)
        if m:
            fence = m.group(1)
            j = i + 1
            while j < n and not lines[j].strip().startswith(fence):
                j += 1
            j = min(j + 1, n)
            blocks.append(Block("code", "\n".join(lines[i:j]), line_no=i + 1))
            i = j
            continue

        # 标题
        m = _HEADING_RE.match(line)
        if m:
            blocks.append(
                Block(
                    "heading",
                    stripped,
                    level=len(m.group(1)),
                    line_no=i + 1,
                    title=m.group(2).strip(),
                )
            )
            i += 1
            continue

        # 分隔线
        if _HR_RE.match(line):
            blocks.append(Block("hr", "---", line_no=i + 1))
            i += 1
            continue

        # 表格：连续以 | 开头的行
        if stripped.startswith("|"):
            j = i
            while j < n and lines[j].strip().startswith("|"):
                j += 1
            blocks.append(Block("table", "\n".join(lines[i:j]), line_no=i + 1))
            i = j
            continue

        # 引用块：连续以 > 开头
        if stripped.startswith(">"):
            j = i
            while j < n and lines[j].strip().startswith(">"):
                j += 1
            blocks.append(Block("quote", "\n".join(lines[i:j]), line_no=i + 1))
            i = j
            continue

        # 列表：连续列表项（允许中间有缩进续行）
        if _LIST_RE.match(line):
            j = i
            while j < n:
                cur = lines[j]
                if not cur.strip():
                    # 列表内的空行：看下一行是否还是列表/缩进
                    if j + 1 < n and (_LIST_RE.match(lines[j + 1]) or lines[j + 1].startswith("  ")):
                        j += 1
                        continue
                    break
                if _LIST_RE.match(cur) or cur.startswith((" ", "\t")):
                    j += 1
                    continue
                break
            blocks.append(Block("list", "\n".join(lines[i:j]).rstrip(), line_no=i + 1))
            i = j
            continue

        # 普通段落：连续非空、非特殊行
        j = i
        while j < n:
            cur = lines[j]
            if not cur.strip():
                break
            if (
                _HEADING_RE.match(cur)
                or _HR_RE.match(cur)
                or cur.strip().startswith(("|", ">"))
                or _LIST_RE.match(cur)
                or _FENCE_RE.match(cur)
            ):
                break
            j += 1
        blocks.append(Block("para", "\n".join(lines[i:j]).strip(), line_no=i + 1))
        i = j

    return blocks


def build_section_tree(blocks: list[Block], root_title: str) -> Section:
    """根据 heading 层级建树。标题前的内容挂到虚拟根节点下。"""
    root = Section(level=0, title=root_title, path=[root_title], heading_line="")
    stack: list[Section] = [root]

    for blk in blocks:
        if blk.kind == "heading":
            while stack and stack[-1].level >= blk.level:
                stack.pop()
            parent = stack[-1] if stack else root
            path = [*parent.path, blk.title]
            sec = Section(
                level=blk.level, title=blk.title, path=path, heading_line=blk.text
            )
            parent.children.append(sec)
            stack.append(sec)
        else:
            if blk.kind == "hr":
                continue
            cur = stack[-1] if stack else root
            cur.direct_blocks.append(blk)

    return root


def flatten_sections(root: Section) -> list[Section]:
    out: list[Section] = []

    def walk(s: Section) -> None:
        out.append(s)
        for c in s.children:
            walk(c)

    walk(root)
    return out
