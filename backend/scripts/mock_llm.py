"""本地 mock LLM：OpenAI 兼容的流式端点，用于离线验证整条生成链路。

用途：真实 API key 不可用时，验证 SSE → 校验器 → 引用 → 落库 是否正常。
端口 8899，不对外。

用法：python -m scripts.mock_llm
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI(title="mock-llm")

# 一段符合八查要求的回答：口语开场 + 口头禅×2 + 追问 + 辨证行 + 表格 + 条文卡 + 角标 + 断言结尾
GOOD_ANSWER = """我跟你说，你这个情况啊，就是太阳伤寒，一点都不复杂。怕冷、没有汗、脖子后面发紧，这三个凑在一起，寒邪把表给束住了，对不对？【5】

你看啊，正常人皮肤表面有一层水气，汗要出来，毛孔得开着。寒一束，毛孔闭上，汗出不去，人就抖——这就是恶寒的来路【5】。**关键**：有汗是中风，无汗是伤寒，这一条不能错。

**辨证：太阳伤寒证（寒邪束表、卫阳被遏·麻黄汤证）**

| 主证 | 表现 | 说明 |
|---|---|---|
| 恶寒 | 怕冷明显，加衣不减 | 寒束肌表 |
| 无汗 | 一点汗都没有 | 与中风鉴别的关键 |
| 身痛 | 全身骨节酸痛 | 寒主收引 |
| 脉浮紧 | 轻取即得、绷紧 | 表实之象 |
| 喘 | 部分人有 | 肺气不宣 |

我问你，为什么麻黄用量一定要大于桂枝？你想想看，麻黄开毛孔，是主力；桂枝解肌，是副手。主次一颠倒，汗出不来，病就拖住了。

> 📜 **伤寒论·第35条**「太阳病，头痛发热，身疼腰痛，骨节疼痛，恶风无汗而喘者，麻黄汤主之。」
> **原方**：麻黄(三两) 桂枝(二两) 杏仁(七十个) 炙甘草(一两)
> **倪师临床**：麻黄(3钱) 桂枝(2钱) 杏仁(3钱) 炙甘草(2钱)

**注意**：麻黄汤发汗力猛，心脏病患者禁用；服后汗出即止，不可再服。

记住了，太阳伤寒就一条路——开表发汗，就这么简单。"""

BAD_ANSWER = """这个嘛，是太阳伤寒。用麻黄汤就可以了。"""


def _chunk(delta: dict[str, Any], finish: str | None = None) -> str:
    payload: dict[str, Any] = {
        "id": "chatcmpl-mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "mock-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/v1/chat/completions")
async def chat(body: dict[str, Any]) -> Any:
    stream = bool(body.get("stream"))
    # 用一个特殊标记控制返回"坏答案"，便于测试校验器
    text = GOOD_ANSWER
    for m in body.get("messages", []):
        content = m.get("content")
        if isinstance(content, str) and "MOCK_BAD" in content:
            text = BAD_ANSWER
            break

    if not stream:
        return {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": "mock-model",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 3000, "completion_tokens": 400, "total_tokens": 3400},
        }

    async def gen():
        # 按 18 字一片切开，模拟真实流式节奏
        step = 18
        for i in range(0, len(text), step):
            yield _chunk({"content": text[i : i + step]})
            await asyncio.sleep(0.012)
        yield _chunk({}, finish="stop")
        usage = {
            "id": "chatcmpl-mock",
            "choices": [],
            "usage": {"prompt_tokens": 3000, "completion_tokens": 400, "total_tokens": 3400},
        }
        yield f"data: {json.dumps(usage, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "mock-model", "object": "model"}]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8899, log_level="warning")
