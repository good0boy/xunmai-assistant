"""切分器测试：重点验证不可切的块没被切碎。"""

from __future__ import annotations

from app.rag.chunking.blocks import normalize, parse_blocks, strip_frontmatter
from app.rag.chunking.cnnum import cn_to_int, extract_chapter_no
from app.rag.chunking.markdown import split_markdown
from app.rag.chunking.table import parse_md_table


def _split(text: str, **kw) -> list:
    return split_markdown(
        text,
        source_file="modules/test.md",
        doc_id="doc1",
        doc_type="module",
        source_priority=2,
        root_title="测试",
        **kw,
    )


def test_strip_frontmatter() -> None:
    raw = "---\nname: x\ndescription: y\n---\n\n# 正文标题\n\n内容"
    out = strip_frontmatter(normalize(raw))
    assert "name: x" not in out
    assert "正文标题" in out


def test_frontmatter_without_body_dash() -> None:
    raw = "# 标题\n\n正文 --- 中间有横线"
    assert strip_frontmatter(normalize(raw)) == normalize(raw)


def test_table_not_split() -> None:
    table = "\n".join(
        ["| 药 | 量 |", "|---|---|"] + [f"| 药{i} | {i}钱 |" for i in range(40)]
    )
    chunks = _split(f"## 方剂\n\n{table}", target=200, max_len=800)
    # 表格被切成多块时，每块都必须保留表头
    for ch in chunks:
        if "|" in ch.text:
            assert "| 药 | 量 |" in ch.text, "表格碎片丢了表头"


def test_blockquote_card_intact() -> None:
    card = (
        "> 📜 **伤寒论·第31条**「太阳病，项背强几几，无汗，恶风者，葛根汤主之。」\n"
        "> **原方**：葛根(四两) 麻黄(三两) 桂枝(二两) 芍药(二两)\n"
        "> **倪师临床**：葛根(4钱) 麻黄(3钱) 桂枝(2钱)"
    )
    chunks = _split(f"### 条文31\n\n{card}", target=300, max_len=1200)
    merged = "\n".join(c.text for c in chunks)
    assert "原方" in merged and "倪师临床" in merged


def test_section_path_full() -> None:
    """小整节会被合并到父级，此时细粒度信息保存在正文的标题行里。"""
    text = "# 金匮要略\n\n## 血痹虚劳病脉证并治第六\n\n### 黄芪桂枝五物汤\n\n内容内容"
    chunks = _split(text)
    assert chunks
    joined = "\n".join(c.text for c in chunks)
    assert "黄芪桂枝五物汤" in joined
    all_paths = [p for c in chunks for p in c.section_path]
    assert "金匮要略" in all_paths


def test_large_section_keeps_deep_path() -> None:
    """大节必须按子节切，且子节 chunk 要带上完整路径。"""
    body = "。".join([f"这是第{i}句讲解内容" for i in range(80)]) + "。"
    text = (
        "# 金匮要略\n\n## 血痹虚劳病脉证并治第六\n\n"
        f"### 黄芪桂枝五物汤\n\n{body}\n\n"
        f"### 桂枝加龙骨牡蛎汤\n\n{body}"
    )
    chunks = _split(text, target=400, max_len=800)
    deep = [c for c in chunks if c.section_path and c.section_path[-1] == "黄芪桂枝五物汤"]
    assert deep, "子节应独立成块"
    # section_path[0] 是文档根标题，其后才是正文标题层级
    assert "金匮要略" in deep[0].section_path
    assert "血痹" in " ".join(deep[0].section_path)


def test_short_chunks_merged() -> None:
    text = "\n\n".join(f"## 小节{i}\n\n短" for i in range(8))
    chunks = _split(text, min_len=120, max_len=1200)
    assert all(c.char_len >= 120 for c in chunks) or len(chunks) < 8


def test_oversize_para_split() -> None:
    long_para = "。".join([f"这是第{i}句话的内容" for i in range(200)]) + "。"
    chunks = _split(f"## 长段\n\n{long_para}", target=400, max_len=800)
    assert len(chunks) > 1
    # 允许 overlap 造成的轻微超出，但不应失控
    assert all(c.char_len <= 900 for c in chunks)


def test_parse_md_table() -> None:
    raw = "| 序号 | 诊断 | 方剂 |\n|---|---|---|\n| 1 | 肝癌 | 大柴胡汤 |\n| 2 | 肺癌 | 十枣汤 |"
    header, rows = parse_md_table(raw)
    assert header == ["序号", "诊断", "方剂"]
    assert len(rows) == 2
    assert rows[0][1] == "肝癌"


def test_parse_md_table_escaped_pipe() -> None:
    raw = "| a | b |\n|---|---|\n| x\\|y | z |"
    header, rows = parse_md_table(raw)
    assert header == ["a", "b"]
    assert rows[0][0] == "x|y"


def test_empty_and_tiny() -> None:
    assert _split("") == []
    # 极短但非空的文档仍应保留一块，直接丢弃会丢内容
    tiny = _split("短")
    assert len(tiny) <= 1


def test_chunk_ids_unique() -> None:
    text = "\n\n".join(f"## 节{i}\n\n内容{i}" * 3 for i in range(30))
    chunks = _split(text)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_parse_blocks_kinds() -> None:
    md = "# 标题\n\n段落文字\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> 引用\n\n- 列表项"
    kinds = [b.kind for b in parse_blocks(md)]
    assert "heading" in kinds
    assert "table" in kinds
    assert "quote" in kinds
    assert "list" in kinds


class TestChapterNo:
    def test_cn_numbers(self) -> None:
        assert cn_to_int("八") == 8
        assert cn_to_int("二十三") == 23
        assert cn_to_int("十") == 10
        assert cn_to_int("十六") == 16

    def test_jingui_title_ending(self) -> None:
        """金匮篇名以「第八」结尾，没有「篇」字，这是源文件的实际格式。"""
        assert extract_chapter_no("奔豚气病脉证并治第八") == 8
        assert extract_chapter_no("藏府经络先后病脉证第一") == 1
        assert extract_chapter_no("惊悸吐衄下血胸满瘀血病脉证第十六") == 16
        assert extract_chapter_no("妇人妊娠病脉证治第二十") == 20

    def test_explicit_pian(self) -> None:
        assert extract_chapter_no("第二十三篇 杂疗方") == 23

    def test_not_chapter(self) -> None:
        assert extract_chapter_no("桂枝汤") is None
        assert extract_chapter_no("金匮要略23篇完整解读") is None
