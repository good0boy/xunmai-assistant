"""对话主流程：检索 → 组装 → 流式生成 → 校验 → 落库。

以 (event, payload) 元组流式产出，由 API 层转成 SSE。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from app.core.config import settings
from app.core.errors import KBUnavailableError
from app.core.logging import get_trace_id, log_event, new_trace_id
from app.llm.client import get_client
from app.rag.prompts import assembler, validator
from app.rag.retrieve import pipeline
from app.services import conversation_service as conv
from app.services import safety

logger = logging.getLogger(__name__)

Event = tuple[str, dict[str, Any]]


async def _save_partial(mid: str, content: str) -> None:
    """把已生成的部分正文落库，并把状态置为 partial。"""
    from app.db.models import Message
    from app.db.session import session_scope

    async with session_scope(write=True) as s:
        m = await s.get(Message, mid)
        if m:
            m.content = content
            m.status = "partial"


async def _load_used_endings(cid: str) -> list[str]:
    info = await conv.get_conversation(cid)
    if not info:
        return []
    return list(info.get("meta", {}).get("used_endings") or [])


async def _save_used_endings(cid: str, ending: str | None) -> None:
    if not ending:
        return
    info = await conv.get_conversation(cid)
    if not info:
        return
    meta = info.get("meta") or {}
    used = list(meta.get("used_endings") or [])
    if ending not in used:
        used.append(ending)
    meta["used_endings"] = used[-20:]
    from app.db.models import Conversation
    from app.db.session import session_scope

    async with session_scope(write=True) as s:
        row = await s.get(Conversation, cid)
        if row:
            row.meta_json = json.dumps(meta, ensure_ascii=False)


async def stream_chat(
    *,
    conversation_id: str | None,
    content: str,
    image_ids: list[str] | None = None,
    with_citations: bool = True,
) -> AsyncIterator[Event]:
    new_trace_id()
    t0 = time.perf_counter()

    if not settings.llm_configured:
        yield "error", {
            "code": "LLM_NOT_CONFIGURED",
            "message": "主模型未配置。请在 .env 中填写 LLM_BASE_URL 与 LLM_API_KEY。",
            "retryable": False,
        }
        return

    # ---------- 会话 ----------
    cid = conversation_id
    if not cid or await conv.get_conversation(cid) is None:
        created = await conv.create_conversation(model=settings.llm_model)
        cid = created["id"]

    # 历史必须在写入本轮消息之前取，否则当前提问会被重复带进 prompt
    history = await conv.get_history_for_prompt(
        cid, limit=max(2, settings.history_max_turns * 2)
    )
    used_endings = await _load_used_endings(cid)

    # ---------- 安全检测 ----------
    red = safety.detect_red_flag(content)
    disc_level, disc_text = safety.pick_disclaimer(content, red)

    # ---------- 检索 ----------
    t_ret = time.perf_counter()
    try:
        retrieval = await pipeline.retrieve(content)
    except KBUnavailableError as exc:
        # 向量库挂了：必须明确报错，不能让检索返回 0 条后告诉用户
        # 「本知识库没有覆盖该问题」—— 那是「悄悄产生错误结果」。
        # 用户消息照常落库，不能凭空消失。
        logger.error("检索失败（知识库不可用）: %s", exc)
        await conv.add_message(cid, "user", content, image_ids=image_ids)
        yield "error", {
            "code": "KB_UNAVAILABLE",
            "message": f"知识库暂时不可用：{exc}",
            "retryable": True,
        }
        return
    retrieval_ms = int((time.perf_counter() - t_ret) * 1000)
    hits = retrieval.hits

    yield "retrieval", {
        "degradation_level": retrieval.level,
        "covered": retrieval.covered,
        "hits": [
            {
                "n": i + 1,
                "chunk_id": h.chunk_id,
                "label": assembler.source_label(h),
                "source": h.payload.get("source_file"),
                "section": h.section_path_str,
                "doc_type": h.payload.get("doc_type"),
                "score": round(h.rrf_score, 4),
            }
            for i, h in enumerate(hits)
        ],
    }

    # ---------- 图像 ----------
    vision_block: str | None = None
    if image_ids:
        vision_block = await _build_vision_block(image_ids)
        if vision_block:
            yield "vision", {"available": settings.vision_enabled, "block": vision_block[:600]}
    elif red.triggered:
        pass

    # ---------- 用户消息落库 ----------
    user_mid = await conv.add_message(
        cid,
        "user",
        content,
        image_ids=image_ids,
        trace={"red_flag": red.triggered, "level": red.level} if red.triggered else None,
    )
    assistant_mid = await conv.add_message(cid, "assistant", "", status="streaming")

    yield "meta", {
        "conversation_id": cid,
        "user_message_id": user_mid,
        "assistant_message_id": assistant_mid,
        "model": settings.llm_model,
        "trace_id": get_trace_id(),
    }

    # ---------- 组装 prompt ----------
    uncovered = (not hits) and not red.triggered
    prompt = assembler.build_messages(
        query=content,
        hits=hits,
        history=history,
        used_endings=used_endings,
        vision_block=vision_block,
        uncovered=uncovered,
        insufficient=len(hits) <= 1,
    )

    if red.triggered:
        prompt.messages.insert(
            1,
            {
                "role": "system",
                "content": (
                    f"【安全覆盖】用户描述属于紧急情况（{ '、'.join(red.labels) }）。"
                    "正文第一段必须是用加粗写的立即就医提示，然后才可以谈学术内容，"
                    "且**不得给出任何方剂剂量**。此场景下不要求辨证行和条文卡片。"
                ),
            },
        )

    # ---------- 流式生成 ----------
    client = get_client()
    buf: list[str] = []
    usage: dict[str, Any] = {}
    finish_reason: str | None = None

    # 红旗提示先于模型输出抵达用户，不依赖模型听话
    if red.triggered:
        banner = red.banner() + "\n\n"
        buf.append(banner)
        yield "delta", {"text": banner}

    try:
        async for chunk in client.stream_chat(prompt.messages):
            if chunk["type"] == "delta":
                text = chunk["text"]
                buf.append(text)
                yield "delta", {"text": text}
            elif chunk["type"] == "usage":
                usage = chunk.get("usage") or {}
            elif chunk["type"] == "done":
                finish_reason = chunk.get("finish_reason")
    except asyncio.CancelledError:
        # 客户端断开（关页面 / 点停止 / 弱网断流）时 API 层会 cancel 本协程。
        # CancelledError 继承 BaseException，不会被下面的 except Exception 捕获，
        # 不兜底就会在库里留下 status="streaming" 的孤儿消息（正文为空，
        # 前端又把它当 done 显示）。
        partial = "".join(buf)
        if partial:
            # 当前 task 已处于取消态，直接 await 会被立刻再次取消，
            # 所以丢到独立 task 里跑，等它落库完成。
            saver = asyncio.create_task(_save_partial(assistant_mid, partial))
            try:
                await asyncio.wait_for(asyncio.shield(saver), timeout=5)
            except Exception:  # noqa: BLE001
                logger.warning("中断后落库未完成", exc_info=True)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("生成失败")
        partial = "".join(buf)
        await conv.finish_message(
            assistant_mid,
            status="partial" if partial else "error",
            error_code=type(exc).__name__,
        )
        if partial:
            await _save_partial(assistant_mid, partial)
        yield "error", {
            "code": "LLM_ERROR",
            "message": str(exc)[:200],
            "retryable": True,
            "partial": bool(partial),
        }
        return

    text = "".join(buf)

    # ---------- 校验与修复 ----------
    result = validator.validate(
        text,
        n_citations=len(hits),
        used_endings=used_endings,
        require_formula=not red.triggered,
        red_flag=red.triggered,
    )

    if result.issues:
        log_event(logger, "八查未通过", issues=result.issues[:6])
        fixed = await _try_fix(prompt.messages, text, result.issues)
        if fixed:
            text = fixed
            yield "replace", {"text": text}
            result = validator.validate(
                text,
                n_citations=len(hits),
                used_endings=used_endings,
                require_formula=not red.triggered,
                red_flag=red.triggered,
            )

    # 确定性清理：剥越界角标与路径泄漏，不依赖模型
    if with_citations:
        text = validator.strip_leaks(text)
    text = validator.strip_leaks(text)

    # ---------- 保存 ----------
    from app.db.models import Message
    from app.db.session import session_scope

    async with session_scope(write=True) as s:
        m = await s.get(Message, assistant_mid)
        if m:
            m.content = text
            m.status = "done"
            m.tokens_in = int(usage.get("prompt_tokens") or 0) or None
            m.tokens_out = int(usage.get("completion_tokens") or 0) or None
            m.cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens") or 0) or None
            m.latency_ms = int((time.perf_counter() - t0) * 1000)
            m.trace_json = json.dumps(
                {
                    **(retrieval.to_trace_payload() if with_citations else {}),
                    "issues": result.issues,
                    "red_flag": red.triggered,
                },
                ensure_ascii=False,
            )

    if with_citations:
        await conv.save_citations(assistant_mid, prompt.citations)
        for c in prompt.citations:
            yield "citation", {
                "n": c["n"],
                "chunk_id": c["chunk_id"],
                "label": c["label"],
                "snippet": c["snippet"],
                "source": c["source_file"],
                "section": c["section_path"],
                "source_group": c.get("source_group", "nihai"),
                "origin": c.get("origin", ""),
                "judge_cites": c.get("judge_cites") or [],
            }

    # ---------- 免责声明：服务端注入 ----------
    yield "disclaimer", {"level": disc_level, "text": disc_text}

    __, ending = validator._check_endings(text, used_endings)
    await _save_used_endings(cid, ending)

    elapsed = int((time.perf_counter() - t0) * 1000)
    # 分段耗时：用来一眼区分「检索慢」还是「模型慢」
    _traces = getattr(retrieval, "traces", []) or []
    log_event(
        logger,
        "chat done",
        level=retrieval.level,
        hits=len(hits),
        chars=len(text),
        issues=len(result.issues),
        red_flag=red.triggered,
        elapsed_ms=elapsed,
        retrieval_ms=retrieval_ms,
        embed_ms=round(sum(t.embed_ms for t in _traces), 1),
        search_ms=round(sum(t.search_ms for t in _traces), 1),
        rerank_ms=round(sum(t.rerank_ms for t in _traces), 1),
        tokens_in=usage.get("prompt_tokens"),
        tokens_out=usage.get("completion_tokens"),
        cache_hit=usage.get("prompt_cache_hit_tokens"),
    )

    yield "done", {
        "assistant_message_id": assistant_mid,
        "finish_reason": finish_reason or "stop",
        "usage": {
            "in": usage.get("prompt_tokens"),
            "out": usage.get("completion_tokens"),
            "cache_hit": usage.get("prompt_cache_hit_tokens"),
        },
        "elapsed_ms": elapsed,
        "issues": result.issues,
    }


async def _try_fix(
    messages: list[dict[str, Any]], text: str, issues: list[str]
) -> str | None:
    """带着具体问题再请求一次，只补格式。"""
    fix_msgs = [
        *messages,
        {"role": "assistant", "content": text},
        {"role": "user", "content": validator.build_fix_message(issues)},
    ]
    try:
        res = await get_client().chat(
            fix_msgs, temperature=0.3, max_tokens=settings.llm_max_tokens
        )
        fixed = (res.get("content") or "").strip()
        return fixed if len(fixed) > len(text) * 0.5 else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("格式修复调用失败: %s", exc)
        return None


async def _build_vision_block(image_ids: list[str]) -> str | None:
    from jinja2 import Template

    from app.services.image_service import analyze_images

    images = await conv.get_images(image_ids)
    if not images:
        return None

    vision = await analyze_images(images)
    if not vision:
        return (
            "【图像】用户上传了图片，但当前未启用图像分析。请让用户用文字补充："
            "舌质颜色、舌体（胖大/瘦薄/齿痕）、舌苔（颜色/厚薄/腻腐）、面色。"
        )

    tpl = (assembler.PROMPT_DIR / "vision.md.j2")
    if not tpl.exists():
        return None
    return (
        Template(tpl.read_text(encoding="utf-8"))
        .render(
            v=vision.get("data") or {},
            low_confidence=vision.get("confidence") == "low",
            confidence_note=vision.get("confidence_note", ""),
        )
        .strip()
    )
