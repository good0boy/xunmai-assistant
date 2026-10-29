"""下载四批知识库补充数据源。

全部落到 kb_src/external/<slug>/，并写一份 _SOURCES.md 记录来源、许可、用途。
国内走 hf-mirror.com，GitHub 走 raw / codeload。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "kb_src" / "external"


def get(url: str, dest: Path, *, timeout: int = 300) -> int:
    """下载到 dest，返回字节数。已存在且非空则跳过。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [skip] {dest.name} ({dest.stat().st_size:,}B)")
        return dest.stat().st_size
    print(f"  [get ] {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "xunmai-kb/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    dest.write_bytes(data)
    print(f"         -> {dest.name} ({len(data):,}B)")
    return len(data)


def batch1() -> None:
    print("\n=== 第一批：中医典籍原文（wobure/zhongyi-books）===")
    dest = EXT / "zhongyi-books"
    books = [
        "伤寒论.md", "金匮要略.md", "本草纲目.md", "神农本草经.md",
        "千金要方.md", "千金翼方.md", "肘后备急方.md", "针灸甲乙经.md",
        "黄帝内经_素问.md", "黄帝内经_灵枢.md", "黄帝八十一难经.md",
        "中药基本理论知识.md", "中医养生学.md", "中医伤科按摩学.md",
        "中医眼科备读.md", "经络.md", "腧穴.md", "穴位.md",
        "邹孟城三十年临证经验集.md", "自我调养巧治病.md", "_十二经脉_动画图.md",
    ]
    for b in books:
        get(
            f"https://hf-mirror.com/datasets/wobure/zhongyi-books/resolve/main/books/{urllib.parse.quote(b)}",
            dest / b,
        )

    print("\n--- 第一批附加：qmzz 汉唐方书 89 首 ---")
    qm = EXT / "qmzz-ht-formulas"
    try:
        from huggingface_hub import list_repo_files

        # 该仓库在 GitHub，先试 GitHub API 树
        url = "https://api.github.com/repos/qmzz/ni-haisha-tcm-skill/git/trees/main?recursive=1"
        with urllib.request.urlopen(url, timeout=60) as r:
            tree = json.loads(r.read())
        paths = [t["path"] for t in tree.get("tree", []) if t["type"] == "blob"]
        ht = [p for p in paths if "ht_formulas" in p and p.endswith(".md")]
        print(f"  找到汉唐方文件 {len(ht)} 个")
        for p in ht:
            get(
                f"https://raw.githubusercontent.com/qmzz/ni-haisha-tcm-skill/main/{urllib.parse.quote(p)}",
                qm / Path(p).name,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] qmzz 汉唐方: {exc}")


def batch2() -> None:
    print("\n=== 第二批：评测基准 ===")
    dest = EXT / "_eval"
    # LingLan（ModelScope 与 HF 同源，先试 HF 镜像）
    try:
        from huggingface_hub import list_repo_files

        files = list_repo_files("TCMLLM/LingLan", repo_type="dataset", endpoint="https://hf-mirror.com")
        print(f"  LingLan 文件 {len(files)} 个，示例: {files[:5]}")
        for f in files:
            if f.endswith((".json", ".jsonl", ".md", ".txt", ".csv")):
                get(
                    f"https://hf-mirror.com/datasets/TCMLLM/LingLan/resolve/main/{urllib.parse.quote(f)}",
                    dest / "LingLan" / Path(f).name,
                )
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] LingLan: {exc}")

    # TCMQA 题库
    try:
        from huggingface_hub import list_repo_files

        files = list_repo_files("TechTCM/TCMQA", repo_type="dataset", endpoint="https://hf-mirror.com")
        print(f"  TCMQA 文件 {len(files)} 个，示例: {files[:5]}")
        for f in files:
            if f.endswith((".json", ".jsonl", ".csv", ".parquet")):
                get(
                    f"https://hf-mirror.com/datasets/TechTCM/TCMQA/resolve/main/{urllib.parse.quote(f)}",
                    dest / "TCMQA" / Path(f).name,
                )
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] TCMQA: {exc}")


def batch3() -> None:
    print("\n=== 第三批：判语库（出处可回溯）===")
    dest = EXT / "nihai-judges"
    base = "https://raw.githubusercontent.com/Renhuai123/nihai-renji-corpus/main"
    for f in ["data/entries.jsonl", "data/corpus-rag.md", "data/schema.md",
              "data/corrections-ledger.jsonl", "LICENSE.md", "README.md"]:
        get(f"{base}/{f}", dest / Path(f).name)


def batch4() -> None:
    print("\n=== 第四批：QA few-shot 素材 ===")
    dest = EXT / "tcm-qa-fewshot"
    try:
        from huggingface_hub import list_repo_files

        files = list_repo_files(
            "xihao1/Traditional-Chinese-Medicine-Knowledge",
            repo_type="dataset",
            endpoint="https://hf-mirror.com",
        )
        print(f"  文件 {len(files)} 个: {files[:5]}")
        for f in files:
            if f.endswith((".json", ".jsonl", ".parquet", ".csv")):
                get(
                    f"https://hf-mirror.com/datasets/xihao1/Traditional-Chinese-Medicine-Knowledge/resolve/main/{urllib.parse.quote(f)}",
                    dest / Path(f).name,
                )
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] xihao1 QA: {exc}")


def write_sources() -> None:
    EXT.mkdir(parents=True, exist_ok=True)
    (EXT / "_SOURCES.md").write_text(
        """# 外部知识库数据源

| 目录 | 来源 | 许可 | 用途 | 批次 |
|---|---|---|---|---|
| `zhongyi-books/` | HF `wobure/zhongyi-books` | 仅供学习研究 | 经典原文，单独建集 `tcm_classics` | 一 |
| `qmzz-ht-formulas/` | GitHub `qmzz/ni-haisha-tcm-skill` | 见仓库 | 汉唐方书 89 首，并入 `tcm_classics` | 一 |
| `_eval/LingLan/` | HF `TCMLLM/LingLan` | 专家基准 | 评测集扩充 | 二 |
| `_eval/TCMQA/` | HF `TechTCM/TCMQA` | 见仓库 | 知识覆盖度体检 | 二 |
| `nihai-judges/` | GitHub `Renhuai123/nihai-renji-corpus` | 见仓库 | 判语出处可回溯，低优先级源 | 三 |
| `tcm-qa-fewshot/` | HF `xihao1/Traditional-Chinese-Medicine-Knowledge` | apache-2.0 | few-shot 示例 | 四 |

**内网自用口径**：以上均为学习研究用途，公开分发前需重新评估版权。
""",
        encoding="utf-8",
    )
    print("\n已写 _SOURCES.md")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    t0 = time.time()
    if which in ("all", "1"):
        batch1()
    if which in ("all", "2"):
        batch2()
    if which in ("all", "3"):
        batch3()
    if which in ("all", "4"):
        batch4()
    write_sources()
    print(f"\n完成，耗时 {time.time()-t0:.0f}s")
