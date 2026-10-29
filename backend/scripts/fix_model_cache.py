"""修复 huggingface_hub 在 Windows 上创建符号链接失败留下的 0 字节缓存文件。

现象：blobs/ 里有完整文件，snapshots/ 里全是 0 字节，
      fastembed 校验时判定 "Files have been corrupted during downloading process"。

原理：用 trees/<revision>.json 里的 blob_id / lfs_sha256 映射，把 blobs 内容
      复制成 snapshot 目录下的真实文件。

用法：python -m scripts.fix_model_cache
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

CACHE = Path("./_local/models")


def fix_repo(repo_dir: Path) -> tuple[int, int]:
    """返回 (已修复数, 仍缺失/损坏数)。"""
    trees = repo_dir / "trees"
    blobs = repo_dir / "blobs"
    snaps = repo_dir / "snapshots"
    if not (trees.exists() and blobs.exists() and snaps.exists()):
        return 0, 0

    fixed = 0
    broken = 0

    for meta_file in trees.glob("*.json"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        files = meta.get("files", {})
        rev = meta_file.stem
        snap_dir = snaps / rev
        if not snap_dir.exists():
            snap_dir = next(snaps.iterdir(), None) if snaps.exists() else None
        if snap_dir is None:
            continue

        for name, info in files.items():
            target = snap_dir / name
            expected = info.get("size", 0)
            if target.exists() and target.stat().st_size == expected and expected > 0:
                continue

            # LFS 文件用 lfs_sha256 做 blob 名，普通文件用 blob_id
            candidates = [
                blobs / info.get("lfs_sha256", ""),
                blobs / info.get("blob_id", ""),
            ]
            src = next((c for c in candidates if c.is_file() and c.stat().st_size > 0), None)
            if src is None:
                broken += 1
                print(f"  [缺] {name} (期望 {expected} 字节，blobs 中未找到)")
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            shutil.copyfile(src, target)
            fixed += 1
            print(f"  [修] {name} <- {src.name[:12]}… ({src.stat().st_size:,} 字节)")

    return fixed, broken


def main() -> None:
    if not CACHE.exists():
        print(f"缓存目录不存在: {CACHE.resolve()}")
        sys.exit(1)

    total_fixed = 0
    total_broken = 0
    for repo_dir in CACHE.glob("models--*"):
        print(f"{repo_dir.name}:")
        f, b = fix_repo(repo_dir)
        total_fixed += f
        total_broken += b

    print(f"\n修复 {total_fixed} 个文件，缺失 {total_broken} 个")
    if total_broken:
        sys.exit(2)


if __name__ == "__main__":
    main()
