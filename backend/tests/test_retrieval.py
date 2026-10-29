"""检索链路测试：RRF、实体加分、校验器、实体抽取。"""

from __future__ import annotations

from app.rag.extract.dicts import Dicts
from app.rag.extract.entities import EntityExtractor
from app.rag.prompts import validator
from app.rag.retrieve.hybrid import Hit, apply_entity_boost, dedupe_by_doc, rrf_fuse


def _hit(cid: str, doc_id: str = "d1", formulas: list[str] | None = None) -> Hit:
    return Hit(
        chunk_id=cid,
        score=0.0,
        payload={"doc_id": doc_id, "formula_names": formulas or []},
    )


class TestRRF:
    def test_merges_and_ranks(self) -> None:
        a = [_hit("x"), _hit("y"), _hit("z")]
        b = [_hit("y"), _hit("z"), _hit("w")]
        fused = rrf_fuse([a, b], k=60)
        ids = [h.chunk_id for h in fused]
        # y 在两个列表里都靠前，应该排第一
        assert ids[0] == "y"
        assert set(ids) == {"x", "y", "z", "w"}

    def test_weights(self) -> None:
        a = [_hit("x")]
        b = [_hit("y")]
        fused = rrf_fuse([a, b], k=60, weights=[1.0, 3.0])
        assert fused[0].chunk_id == "y"

    def test_empty(self) -> None:
        assert rrf_fuse([[], []]) == []


class TestBoost:
    def test_formula_boost(self) -> None:
        h1 = _hit("a", formulas=["桂枝汤"])
        h2 = _hit("b", formulas=["麻黄汤"])
        h1.rrf_score = h2.rrf_score = 0.01
        apply_entity_boost([h1, h2], formulas=["桂枝汤"], diseases=[], acupoints=[])
        assert h1.rrf_score > h2.rrf_score
        assert h1.boosted > 0

    def test_no_entity_no_change(self) -> None:
        h = _hit("a")
        h.rrf_score = 0.02
        apply_entity_boost([h], formulas=[], diseases=[], acupoints=[])
        assert h.boosted == 0.0


class TestDedupeByDoc:
    def test_limits_per_doc(self) -> None:
        hits = [_hit(f"c{i}", doc_id="same") for i in range(10)]
        out = dedupe_by_doc(hits, max_per_doc=3)
        assert len(out) == 3

    def test_keeps_other_docs(self) -> None:
        hits = [_hit("a1", "d1"), _hit("a2", "d1"), _hit("b1", "d2")]
        out = dedupe_by_doc(hits, max_per_doc=1)
        assert {h.chunk_id for h in out} == {"a1", "b1"}


class TestValidator:
    def _good(self) -> str:
        return (
            "我跟你说，这个就是太阳伤寒，你看啊，无汗恶寒体痛，对不对？【1】"
            "你想想看，为什么无汗？寒把毛孔束住了嘛。\n\n"
            "**辨证：太阳伤寒（寒邪束表、卫阳被遏·麻黄汤证）**\n\n"
            "| 主证 | 说明 |\n|---|---|\n| 恶寒 | 明显 |\n| 无汗 | 关键 |\n\n"
            "> 📜 **伤寒论·第35条**「太阳病，头痛发热，身疼腰痛，骨节疼痛，恶风无汗而喘者，麻黄汤主之。」\n"
            "> **原方**：麻黄(三两) 桂枝(二两) 杏仁(七十个) 炙甘草(一两)\n"
            "> **倪师临床**：麻黄(3钱) 桂枝(2钱)\n\n"
            "就这么简单。"
        )

    def test_good_passes(self) -> None:
        res = validator.validate(self._good(), n_citations=2, used_endings=[])
        assert res.ok, res.issues

    def test_missing_citation_marker(self) -> None:
        """有证据却一个角标都不标，引用形同虚设。"""
        text = self._good().replace("【1】", "")
        res = validator.validate(text, n_citations=2, used_endings=[])
        assert any("角标" in i for i in res.issues), res.issues

    def test_missing_table_flagged(self) -> None:
        text = (
            "我跟你说，这就是太阳病，你看啊，对不对？你想想看？"
            "**辨证：太阳伤寒（寒邪·麻黄汤证）**。就这么简单。"
        )
        res = validator.validate(text, n_citations=1, used_endings=[])
        assert any("重点标注" in i for i in res.issues)

    def test_missing_dangue_flagged(self) -> None:
        text = "我跟你说，**重点**啊。你看啊，对不对？你想想看。就这么简单。| a |\n|---|\n| b |"
        res = validator.validate(text, n_citations=1, used_endings=[])
        assert any("辨证行" in i for i in res.issues)

    def test_repeated_ending_flagged(self) -> None:
        text = self._good()
        res = validator.validate(text, n_citations=2, used_endings=["就这么简单"])
        assert any("已用过" in i for i in res.issues)

    def test_leak_detected(self) -> None:
        text = self._good() + "\n\n详见 modules/04_jingui.md 第 120 行。"
        res = validator.validate(text, n_citations=2, used_endings=[])
        assert any("路径" in i for i in res.issues)
        assert res.hard_fail

    def test_strip_leaks(self) -> None:
        out = validator.strip_leaks("参见 modules/01_shanghan_sun.md 第 88 行")
        assert "modules/" not in out
        assert "第 88 行" not in out

    def test_over_citation_detected(self) -> None:
        text = self._good() + "\n\n这也见于【5】。"
        res = validator.validate(text, n_citations=2, used_endings=[])
        assert any("角标越界" in i for i in res.issues)

    def test_strip_bad_citations(self) -> None:
        out = validator.strip_bad_citations("正常【1】，越界【9】", n_citations=3)
        assert "【1】" in out
        assert "【9】" not in out

    def test_empty(self) -> None:
        res = validator.validate("", n_citations=0, used_endings=[])
        assert res.hard_fail


class TestEntities:
    def _extractor(self) -> EntityExtractor:
        return EntityExtractor(Dicts.load())

    def test_formula_from_dict(self) -> None:
        e = self._extractor().extract("用桂枝汤治疗太阳中风，汗出恶风")
        assert "桂枝汤" in e.formulas
        assert "汗出" in e.symptoms
        assert "恶风" in e.symptoms

    def test_no_sentence_fragment(self) -> None:
        """正则兜底不能把整句切成方名（"更多的滋阴汤" 这类）。"""
        e = self._extractor().extract("这是更多的滋阴汤，你要多用")
        assert all(len(f) <= 6 for f in e.formulas)
        assert not any(f.startswith("更多") for f in e.formulas)

    def test_article_no(self) -> None:
        e = self._extractor().extract("第31条讲项背强几几")
        assert "31" in e.article_nos

    def test_acupoint(self) -> None:
        e = self._extractor().extract("扎足三里和合谷")
        assert "足三里" in e.acupoints
        assert "合谷" in e.acupoints
