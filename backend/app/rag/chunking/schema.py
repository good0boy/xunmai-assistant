"""知识块数据结构。字段与 Qdrant payload 一一对应。"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    source_file: str
    doc_type: str
    source_priority: int
    text: str  # 展示用正文
    embed_text: str = ""  # 向量化用（带上下文前缀）

    section_path: list[str] = field(default_factory=list)
    section_path_str: str = ""
    heading_level: int = 0
    ordinal: int = 0

    chapter_no: int | None = None
    ordinal_in_chapter: int | None = None
    article_no: str | None = None
    article_no_note: str | None = None

    formula_names: list[str] = field(default_factory=list)
    herbs: list[str] = field(default_factory=list)
    acupoints: list[str] = field(default_factory=list)
    diseases: list[str] = field(default_factory=list)
    symptoms: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    case_serial: str | None = None
    case_tag_raw: str | None = None
    case_tag_fixed: str | None = None
    tag_quality: str | None = None
    row_raw: list[str] | None = None

    # 知识库分组与同源标识（经典原文 / 倪师解读 / 判语）
    source_group: str = "nihai"
    origin: str = ""
    # 判语出处（B 站视频号 + 时间戳），仅判语组有值
    judge_cites: list[dict[str, Any]] = field(default_factory=list)

    char_len: int = 0
    token_est: int = 0
    content_hash: str = ""

    def finalize(self) -> Chunk:
        self.char_len = len(self.text)
        self.token_est = int(self.char_len * 0.7)
        self.content_hash = hashlib.sha1(self.text.encode("utf-8")).hexdigest()
        self.section_path_str = " > ".join(self.section_path)
        if not self.embed_text:
            self.embed_text = self.text
        return self

    def to_payload(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("embed_text", None)
        return d


def make_doc_id(rel_path: str) -> str:
    return hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:16]


def make_chunk_id(doc_id: str, ordinal: int) -> str:
    return hashlib.sha1(f"{doc_id}:{ordinal}".encode()).hexdigest()[:24]
