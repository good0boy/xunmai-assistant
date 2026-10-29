"""红旗症状检测与免责声明分级。

这两件事都不交给模型：模型不听话时，合规底线不能跟着丢。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.core.config import settings

logger = logging.getLogger(__name__)

CONFIG = Path(__file__).resolve().parents[1] / "config" / "red_flags.yaml"
DISCLAIMER_DIR = Path(__file__).resolve().parents[1] / "prompts" / "disclaimer"

Level = Literal["standard", "severe", "emergency"]


@dataclass
class RedFlagResult:
    triggered: bool = False
    level: Level = "standard"
    labels: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def banner(self) -> str:
        if not self.triggered:
            return ""
        what = "、".join(self.labels)
        return (
            f"🚨 **请注意：你描述的情况（{what}）属于需要立即就医的情形。**"
            "请马上拨打 120 或到最近医院急诊，不要等着看内容、不要自行服药。"
            "下面的内容只是典籍里的记载，**不适用于你现在的紧急情况**。"
        )


@lru_cache(maxsize=1)
def _rules() -> dict[str, Any]:
    if not CONFIG.exists():
        logger.warning("红旗症状配置缺失: %s", CONFIG)
        return {"rules": [], "severe_terms": []}
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def detect_red_flag(text: str) -> RedFlagResult:
    res = RedFlagResult()
    if not settings.red_flag_enabled or not text:
        return res

    for rule in _rules().get("rules", []):
        any_of = rule.get("any_of") or []
        with_any = rule.get("with_any") or []
        if not any(a in text for a in any_of):
            continue
        if with_any and not any(w in text for w in with_any):
            continue
        res.triggered = True
        label = rule.get("label", rule.get("id", ""))
        if label not in res.labels:
            res.labels.append(label)
        if rule.get("note") and rule["note"] not in res.notes:
            res.notes.append(rule["note"])
        # 目前所有规则都是"必须立即就医"级别，统一按 emergency 处理
        res.level = "emergency"

    return res


def has_severe_term(text: str) -> bool:
    terms = _rules().get("severe_terms") or []
    return any(t in text for t in terms)


def pick_disclaimer(text: str, red_flag: RedFlagResult) -> tuple[Level, str]:
    """返回 (级别, 文案)。"""
    if red_flag.triggered and red_flag.level == "emergency":
        return "emergency", _load("emergency.md")
    if red_flag.triggered:
        return "severe", _load("severe.md")
    if settings.disclaimer_mode == "strict":
        return "severe", _load("severe.md")
    if has_severe_term(text):
        return "severe", _load("severe.md")
    return "standard", _load("standard.md")


@lru_cache(maxsize=8)
def _load(name: str) -> str:
    p = DISCLAIMER_DIR / name
    if not p.exists():
        return "⚠️ 本内容仅供中医学习与学术参考，不构成医疗诊断、处方或个体化治疗建议。身体不适请咨询执业中医师。"
    return p.read_text(encoding="utf-8").strip()
