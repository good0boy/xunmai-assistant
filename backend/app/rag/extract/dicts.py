"""领域词典加载与最长匹配。用联合正则实现，避免引入 AC 自动机依赖。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

_STOP_FALLBACK = {"的", "了", "是", "在", "有", "和", "与", "或", "不", "为", "以", "之"}


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # jieba userdict 格式：词 [词频] [词性]
        out.append(line.split()[0].strip())
    return [w for w in out if w]


@dataclass
class Dicts:
    formulas: list[str]
    acupoints: list[str]
    diseases: list[str]
    symptoms: list[str]
    herbs: list[str]
    stopwords: set[str]
    file_hashes: dict[str, str]

    @classmethod
    def load(cls, config_dir: Path | None = None) -> Dicts:
        d = config_dir or CONFIG_DIR
        files = {
            "formulas": d / "formula_dict.txt",
            "acupoints": d / "acupoint_dict.txt",
            "diseases": d / "disease_dict.txt",
            "symptoms": d / "symptom_dict.txt",
            "herbs": d / "herb_dict.txt",
            "stopwords": d / "stopwords.txt",
        }
        loaded: dict[str, list[str]] = {}
        hashes: dict[str, str] = {}
        for key, path in files.items():
            words = _read_lines(path)
            loaded[key] = words
            if path.exists():
                hashes[key] = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
            else:
                hashes[key] = "absent"

        stop = set(loaded.pop("stopwords", [])) or _STOP_FALLBACK
        return cls(stopwords=stop, file_hashes=hashes, **loaded)  # type: ignore[arg-type]

    @cached_property
    def _formula_re(self) -> re.Pattern[str] | None:
        return _compile(self.formulas)

    @cached_property
    def _acupoint_re(self) -> re.Pattern[str] | None:
        return _compile(self.acupoints)

    @cached_property
    def _disease_re(self) -> re.Pattern[str] | None:
        return _compile(self.diseases)

    @cached_property
    def _symptom_re(self) -> re.Pattern[str] | None:
        return _compile(self.symptoms)

    @cached_property
    def _herb_re(self) -> re.Pattern[str] | None:
        return _compile(self.herbs)

    def find_formulas(self, text: str) -> list[str]:
        return _find(self._formula_re, text)

    def find_acupoints(self, text: str) -> list[str]:
        return _find(self._acupoint_re, text)

    def find_diseases(self, text: str) -> list[str]:
        return _find(self._disease_re, text)

    def find_symptoms(self, text: str) -> list[str]:
        return _find(self._symptom_re, text)

    def find_herbs(self, text: str) -> list[str]:
        return _find(self._herb_re, text)

    def all_terms(self) -> set[str]:
        return set(self.formulas) | set(self.acupoints) | set(self.diseases) | set(self.herbs)


def _compile(words: list[str]) -> re.Pattern[str] | None:
    if not words:
        return None
    # 长度降序 → 正则交替分支靠前者优先，实现最长匹配
    uniq = sorted({w for w in words if w}, key=len, reverse=True)
    pattern = "|".join(re.escape(w) for w in uniq)
    return re.compile(pattern)


def _find(pattern: re.Pattern[str] | None, text: str, limit: int = 16) -> list[str]:
    if pattern is None or not text:
        return []
    seen: list[str] = []
    for m in pattern.finditer(text):
        w = m.group(0)
        if w not in seen:
            seen.append(w)
            if len(seen) >= limit:
                break
    return seen
