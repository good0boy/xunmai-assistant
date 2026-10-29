"""会话消息顺序与引用持久化的回归测试。

这些都是「悄悄产生错误结果但不报错」的类型，必须有测试守着：

1. 同秒写入的 user / assistant 消息，顺序必须按真实写入顺序（不能用随机 uuid 排）
2. 引用必须带上 source_group / origin / judge_cites —— 缺了页面刷新后
   「B 站原话出处」整块消失
3. 重写引用不能撞唯一约束
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import MessageCitation  # noqa: E402
from app.db.session import dispose_db, init_db, session_scope  # noqa: E402
from app.services import conversation_service as conv  # noqa: E402


@pytest.fixture(autouse=True)
async def _db():
    await init_db()
    yield
    await dispose_db()


async def _new_cid(title: str = "t") -> str:
    return (await conv.create_conversation(title=title))["id"]


async def test_message_order_follows_write_order():
    """user 与 assistant 几乎必然同秒写入，顺序必须按写入顺序。"""
    cid = await _new_cid()
    await conv.add_message(cid, "user", "第一个问题")
    await conv.add_message(cid, "assistant", "第一个回答")
    await conv.add_message(cid, "user", "第二个问题")
    await conv.add_message(cid, "assistant", "第二个回答")

    msgs = await conv.get_messages(cid)
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "user", "assistant"], f"顺序错乱: {roles}"


async def test_history_for_prompt_keeps_order():
    """送进 LLM 的历史也必须是有序的。"""
    cid = await _new_cid()
    for i in range(6):
        await conv.add_message(cid, "user", f"问题{i}")
        await conv.add_message(cid, "assistant", f"回答{i}")

    hist = await conv.get_history_for_prompt(cid, limit=4)
    # 6 轮共 12 条，limit=4 取的是**最近** 4 条
    assert [h["content"] for h in hist] == ["问题4", "回答4", "问题5", "回答5"]


async def test_get_messages_takes_latest_not_oldest():
    """超过 limit 时必须取最近的消息（原来取的是最旧的，导致历史永久冻结）。"""
    cid = await _new_cid()
    for i in range(10):
        await conv.add_message(cid, "user", f"q{i}")
    msgs = await conv.get_messages(cid, limit=3)
    assert [m["content"] for m in msgs] == ["q7", "q8", "q9"]


async def test_citations_roundtrip_keeps_group_and_judge_cites():
    """引用落库后要能原样读回 —— 这是「页面刷新后 B 站出处不消失」的基础。"""
    cid = await _new_cid()
    mid = await conv.add_message(cid, "assistant", "回答")
    cites = [
        {
            "n": 1,
            "chunk_id": "c1",
            "doc_id": "d1",
            "source_file": "classics/千金要方.md",
            "section_path": "千金要方/风痰",
            "snippet": "片段",
            "score": 0.166,
            "rank": 1,
            "source_group": "classics",
            "origin": "qianjin_yaofang",
            "judge_cites": [
                {
                    "bvid": "BV1rL8xzAEWA",
                    "ep": "15",
                    "section": "1",
                    "t_hms": "00:00:27",
                    "start_sec": "27",
                    "url": "https://www.bilibili.com/video/BV1rL8xzAEWA?t=27",
                }
            ],
        },
        {
            "n": 2,
            "chunk_id": "c2",
            "doc_id": "d2",
            "source_file": "modules/01_shanghan_sun.md",
            "section_path": "太阳病篇",
            "snippet": "片段2",
            "score": 0.15,
            "rank": 2,
            "source_group": "nihai",
            "origin": "shanghan_lun",
            "judge_cites": [],
        },
    ]
    await conv.save_citations(mid, cites)
    got = await conv.get_citations(mid)
    assert len(got) == 2
    assert got[0]["source_group"] == "classics"
    assert got[0]["origin"] == "qianjin_yaofang"
    assert len(got[0]["judge_cites"]) == 1
    assert got[0]["judge_cites"][0]["bvid"] == "BV1rL8xzAEWA"
    assert got[1]["judge_cites"] == []


async def test_resave_citations_does_not_violate_unique_constraint():
    """重写同一消息的引用不能撞 (message_id, n) 唯一约束。"""
    cid = await _new_cid()
    mid = await conv.add_message(cid, "assistant", "回答")
    one = [{"n": 1, "chunk_id": "c1", "score": 0.1, "rank": 1, "source_group": "nihai"}]
    await conv.save_citations(mid, one)
    await conv.save_citations(mid, one)  # 重写
    got = await conv.get_citations(mid)
    assert len(got) == 1


async def test_citation_columns_exist():
    """迁移必须把新列加上 —— 缺列时 save_citations 会直接报错。"""
    async with session_scope() as s:
        cols = {c.name for c in MessageCitation.__table__.columns}
    assert {"source_group", "origin", "judge_cites_json"} <= cols
