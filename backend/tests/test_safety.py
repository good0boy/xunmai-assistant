"""红旗症状与免责分级测试。这是合规底线，必须锁死。"""

from __future__ import annotations

import pytest

from app.services import safety


@pytest.mark.parametrize(
    "text",
    [
        "我胸口疼，出冷汗，还放射到左臂",
        "突然昏倒，口角歪斜，说话不清",
        "半身不遂",
        "喘不上气，嘴唇发紫",
        "大量呕血",
        "高烧不退，脖子硬得低不下头",
        "怀孕三个月，有点出血",
        "宝宝高烧抽搐",
        "肚子疼得像板子一样硬，按下去更疼",
        "我不想活了",
    ],
)
def test_red_flag_triggers(text: str) -> None:
    res = safety.detect_red_flag(text)
    assert res.triggered, f"应触发红旗：{text}"
    assert res.level == "emergency"


@pytest.mark.parametrize(
    "text",
    [
        "我最近有点头晕",
        "小柴胡汤什么时候用",
        "晚上睡不着",
        "手脚冰凉",
        "肚子有点胀",
        "感冒了怕冷",
    ],
)
def test_red_flag_not_over_trigger(text: str) -> None:
    """不能因为是常见症状词就误报，否则用户会被无意义的警告淹没。"""
    res = safety.detect_red_flag(text)
    assert not res.triggered, f"不该触发红旗：{text}"


def test_red_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety.settings, "red_flag_enabled", False)
    res = safety.detect_red_flag("胸口疼，出冷汗")
    assert not res.triggered


def test_disclaimer_levels() -> None:
    red = safety.detect_red_flag("胸口剧烈疼痛，大汗淋漓")
    level, text = safety.pick_disclaimer("胸口剧烈疼痛，大汗淋漓", red)
    assert level == "emergency"
    assert "120" in text

    normal = safety.detect_red_flag("最近有点头晕")
    level2, text2 = safety.pick_disclaimer("最近有点头晕", normal)
    assert level2 == "standard"
    assert "学习" in text2 or "参考" in text2


def test_severe_term() -> None:
    red = safety.detect_red_flag("我父亲查出肺癌")
    level, _ = safety.pick_disclaimer("我父亲查出肺癌", red)
    assert level == "severe"


def test_banner_contains_emergency_signal() -> None:
    red = safety.detect_red_flag("胸口疼，冷汗")
    banner = red.banner()
    assert "120" in banner
    assert "紧急" in banner or "立即" in banner
