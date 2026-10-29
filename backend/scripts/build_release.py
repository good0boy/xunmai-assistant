"""构建单容器发布包（供 publish-mcp 部署）。

产物：dist_release/xunmai-release.tar.gz
  ├── main.py            入口（python main.py 或 uvicorn main:app）
  ├── pyproject.toml     让发布服务识别为 Python 项目
  ├── requirements.txt
  ├── .env               运行配置（含 LLM key，注意分发范围）
  ├── app/               后端代码 + prompts + 领域配置
  ├── web/               前端构建产物
  └── runtime/
      ├── qdrant/        预构建索引（本地模式）
      └── app.db         含 kb_meta 的 SQLite

用法（backend 目录下）：
    python -m scripts.build_release            # 索引已存在则复用
    python -m scripts.build_release --rebuild  # 强制重建索引
    python -m scripts.build_release --no-pack  # 只准备目录，不打包
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # backend/
PROJECT = ROOT.parent                                # 项目根
RELEASE = PROJECT / "dist_release"
BUNDLE = RELEASE / "bundle"
KB_SRC = ROOT / "kb_src" / "nihaixia"
FRONT_DIST = PROJECT / "frontend" / "dist"
MODEL_CACHE = PROJECT / "data" / "models"

REQUIREMENTS = """--index-url https://mirrors.aliyun.com/pypi/simple
--extra-index-url https://pypi.org/simple
--trusted-host mirrors.aliyun.com
--trusted-host pypi.org
fastapi==0.159.1
uvicorn==0.54.0
pydantic==2.14.1
pydantic-settings==2.16.1
sqlalchemy[asyncio]==2.1.6
aiosqlite==0.22.1
httpx==0.28.1
# qdrant-client 与 docker-compose 里的 server v1.12.4 保持同一 minor（1.12.x，
# PyPI 上 1.12 系列最高只发布到 1.12.2）。1.19 客户端 + 1.12 服务端跨 7 个
# minor 曾出现兼容告警，且与「批量写入间歇性丢 dense 向量」的时间窗重合。
qdrant-client==1.12.2
fastembed==0.9.0
jieba==0.42.1
mmh3==5.3.1
pyyaml==6.0.3
jinja2==3.1.6
pillow==11.3.0
python-multipart==0.0.32
tenacity==9.2.1
"""

MAIN_PY = '''"""发布版入口。

两种启动方式都支持：
    python main.py
    uvicorn main:app --host 0.0.0.0 --port $PORT

启动前会把「需要写权限」的运行时数据准备好：
镜像里的文件通常是只读的，而 SQLite 开 WAL、Qdrant 本地模式都要写文件。
能写就原地用，不能写就复制到临时目录，保证服务一定能起来。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _can_write_dir(p: Path) -> bool:
    try:
        probe = p / ".write_probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _can_write_file(p: Path) -> bool:
    try:
        with open(p, "ab"):
            pass
        return True
    except OSError:
        return False


def prepare_runtime() -> None:
    work = Path(tempfile.gettempdir()) / "xunmai_runtime"
    work.mkdir(parents=True, exist_ok=True)

    # ---- SQLite ----
    bundled_db = HERE / "runtime" / "app.db"
    db_path: Path
    if bundled_db.is_file() and _can_write_file(bundled_db):
        db_path = bundled_db
    else:
        db_path = work / "app.db"
        if bundled_db.is_file() and not db_path.exists():
            shutil.copy2(bundled_db, db_path)
        print(f"[runtime] DB 复制到可写位置: {db_path}", flush=True)

    os.environ.setdefault("DB_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    os.environ.setdefault("UPLOAD_DIR", str(work / "uploads"))

    # ---- Qdrant 索引 ----
    bundled_q = HERE / "runtime" / "qdrant"
    q_path = bundled_q
    if bundled_q.is_dir():
        if not _can_write_dir(bundled_q):
            q_path = work / "qdrant"
            if not (q_path / "meta.json").exists():
                shutil.copytree(bundled_q, q_path, dirs_exist_ok=True)
            print(f"[runtime] 索引复制到可写位置: {q_path}", flush=True)
    else:
        # slim 包：索引运行时下载，只能落在可写工作目录
        q_path = work / "qdrant"
    os.environ.setdefault("QDRANT_PATH", str(q_path))

    # ---- 模型缓存：包内不可写就落到工作目录（首次会下载 94MB）----
    bundled_m = HERE / "runtime" / "models"
    if bundled_m.is_dir() and not _can_write_dir(bundled_m):
        os.environ.setdefault("FASTEMBED_CACHE_PATH", str(work / "models"))
        (work / "models").mkdir(parents=True, exist_ok=True)

    # fastembed 只认环境变量，不读 .env。集群若需走镜像必须在这里设。
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def _read_env_value(key: str) -> str:
    """读环境变量，缺失则回退解析 .env。

    ensure_index 在 import app 之前执行，pydantic-settings 还没加载 .env，
    这里必须自己解析一遍。
    """
    val = os.environ.get(key, "").strip()
    if val:
        return val
    env_file = HERE / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            s = line.strip()
            if s.startswith(key + "="):
                return s.split("=", 1)[1].strip()
    return ""


def ensure_index(q_path: Path) -> None:
    """包内不带索引时，从内网源拉取预构建索引。

    平台上传通道有 1MB 限制（nginx client_max_body_size），完整发布包
    约 21MB 传不上去，因此索引单独分发，容器启动时下载。
    多源按顺序尝试，任一成功即返回；全失败则服务以降级模式运行。
    """
    if (q_path / "meta.json").is_file():
        print(f"[bootstrap] 索引已在包内: {q_path}", flush=True)
        return

    raw = _read_env_value("KB_INDEX_SOURCES")
    sources = [s.strip() for s in raw.replace(",", "|").split("|") if s.strip()]
    if not sources:
        print("[bootstrap] 未配置 KB_INDEX_SOURCES，索引缺失（检索将降级）", flush=True)
        return

    import httpx
    import tarfile

    q_path.parent.mkdir(parents=True, exist_ok=True)
    archive = q_path.parent / "qdrant-index.tar.gz"

    for src in sources:
        try:
            print(f"[bootstrap] 下载索引: {src}", flush=True)
            with httpx.Client(
                timeout=httpx.Timeout(300.0, connect=15.0), follow_redirects=True
            ) as client:
                with client.stream("GET", src) as resp:
                    resp.raise_for_status()
                    with open(archive, "wb") as fh:
                        for chunk in resp.iter_bytes(1024 * 1024):
                            fh.write(chunk)
            size = archive.stat().st_size
            print(f"[bootstrap] 下载完成 {size/1024/1024:.1f} MB，解压…", flush=True)
            q_path.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive) as tar:
                try:
                    tar.extractall(q_path, filter="data")
                except TypeError:  # Python < 3.12 无 filter 参数
                    tar.extractall(q_path)
            archive.unlink(missing_ok=True)
            if (q_path / "meta.json").is_file():
                print(f"[bootstrap] 索引就位: {q_path}", flush=True)
                return
            print("[bootstrap] 解压后仍缺 meta.json，换下一个源", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[bootstrap] 源失败 {src}: {exc}", flush=True)

    print("[bootstrap] 所有源均失败，知识库不可用（检索降级为纯向量缺失）", flush=True)


prepare_runtime()
ensure_index(Path(os.environ["QDRANT_PATH"]))

from app.main import app  # noqa: E402


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print(f"[runtime] PROJECT_ROOT={HERE} PORT={port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
'''

PYPROJECT = """[project]
name = "xunmai-assistant-release"
version = "0.1.0"
requires-python = ">=3.11"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
"""


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def check_prereq(rebuild: bool) -> None:
    problems = []
    if not KB_SRC.is_dir():
        problems.append(f"知识库源缺失: {KB_SRC}")
    if not FRONT_DIST.is_dir():
        problems.append(f"前端产物缺失: {FRONT_DIST}（先 cd frontend && npm run build）")
    if problems:
        for p in problems:
            print(f"✗ {p}")
        sys.exit(1)


def build_index(force: bool) -> tuple[Path, Path]:
    """用本地模式建索引，返回 (qdrant_dir, db_path)。"""
    qdrant_dir = BUNDLE / "runtime" / "qdrant"
    db_path = BUNDLE / "runtime" / "app.db"

    if qdrant_dir.exists() and db_path.exists() and not force:
        log(f"复用已有索引: {qdrant_dir} ({dir_size(qdrant_dir)/1024/1024:.1f} MB)")
        return qdrant_dir, db_path

    for p in (qdrant_dir, db_path):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    qdrant_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "QDRANT_MODE": "local",
            "QDRANT_PATH": str(qdrant_dir),
            "DB_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "FASTEMBED_CACHE_PATH": str(MODEL_CACHE),
            "HF_ENDPOINT": env.get("HF_ENDPOINT", "https://hf-mirror.com"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(ROOT),
        }
    )

    log("本地模式建索引（约 5 分钟）…")
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, "-m", "scripts.build_index"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        sys.exit(f"索引构建失败 (exit {r.returncode})")

    log(f"索引完成，耗时 {time.time()-t0:.0f}s，大小 {dir_size(qdrant_dir)/1024/1024:.1f} MB")
    return qdrant_dir, db_path


def copy_code() -> None:
    log("复制后端代码…")
    dst_app = BUNDLE / "app"
    if dst_app.exists():
        shutil.rmtree(dst_app)
    shutil.copytree(
        ROOT / "app",
        dst_app,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # scripts 不需要进发布包，但 mock 之类的调试工具也不带，保持精简

    log("复制前端产物…")
    dst_web = BUNDLE / "web"
    if dst_web.exists():
        shutil.rmtree(dst_web)
    shutil.copytree(FRONT_DIST, dst_web)

    (BUNDLE / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (BUNDLE / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (BUNDLE / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")


def write_env() -> None:
    """生成发布版 .env。相对路径基于进程 cwd（发布根目录）。"""
    src = PROJECT / ".env"
    keep_keys = {
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_TEMPERATURE",
        "LLM_MAX_TOKENS",
        "UTILITY_MODEL",
        "UTILITY_TEMPERATURE",
        "UTILITY_MAX_TOKENS",
        "VLM_ENABLED",
        "VLM_BASE_URL",
        "VLM_API_KEY",
        "VLM_MODEL",
        "EMBED_MODEL",
        "EMBED_DIM",
        "EMBED_QUERY_PREFIX",
        "RAG_RERANK_BACKEND",
        "RAG_QUERY_REWRITE",
        "RAG_DEGRADATION_MAX_LEVEL",
        "RAG_FINAL_K",
        "DISCLAIMER_MODE",
        "RED_FLAG_ENABLED",
        "APP_PASSCODE",
        "ADMIN_TOKEN",
        "KB_INDEX_SOURCES",
    }

    values: dict[str, str] = {}
    if src.exists():
        for line in src.read_text(encoding="utf-8-sig").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            if k.strip() in keep_keys:
                values[k.strip()] = v.strip()

    lines = [
        "# 发布版运行配置（由 build_release 生成）",
        "APP_ENV=prod",
        "LOG_LEVEL=INFO",
        "CORS_ORIGINS=*",
        "",
        "# 向量库：进程内嵌，索引随包交付",
        "QDRANT_MODE=local",
        "QDRANT_PATH=./runtime/qdrant",
        "",
        "# 前端由后端托管",
        "FRONTEND_DIST=./web",
        "",
        "# 可写目录（容器内相对 cwd）",
        "DB_URL=sqlite+aiosqlite:///./runtime/app.db",
        "UPLOAD_DIR=./runtime/uploads",
        "FASTEMBED_CACHE_PATH=./runtime/models",
        "",
        "# 模型下载镜像（首次启动需拉 94MB ONNX）",
        "HF_ENDPOINT=https://hf-mirror.com",
        "",
        "# 索引自举源：包内不带 Qdrant 索引（上传通道有 1MB 限制），",
        "# 容器启动时按顺序尝试下载，逗号或竖线分隔",
        "KB_INDEX_SOURCES=" + values.get("KB_INDEX_SOURCES", ""),
        "",
        "# ---- 以下继承本地配置 ----",
    ]
    for k in sorted(keep_keys):
        if k in values and values[k]:
            lines.append(f"{k}={values[k]}")

    (BUNDLE / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    has_key = bool(values.get("LLM_API_KEY"))
    log(f".env 已生成，LLM key {'已包含' if has_key else '缺失'}")


def _skip_git(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """排除 .git 目录（bundle 里可能残留 git 仓库，会让包体积暴涨）。"""
    parts = info.name.replace("\\", "/").split("/")
    if ".git" in parts:
        return None
    return info


def pack() -> Path:
    out = RELEASE / "xunmai-release.tar.gz"
    if out.exists():
        out.unlink()

    log("打包 tar.gz…")
    with tarfile.open(out, "w:gz") as tar:
        for item in sorted(BUNDLE.iterdir()):
            tar.add(item, arcname=item.name, filter=_skip_git)

    mb = out.stat().st_size / 1024 / 1024
    log(f"完成: {out}  ({mb:.1f} MB)")
    if mb > 36:
        log(f"⚠️ 超过 publish-mcp 的 36MB 上限 {mb:.1f}MB，需要裁剪（索引量化或减块）")
    return out


def make_index_archive() -> Path:
    """把预构建索引打成独立 tar.gz，供容器启动时下载。

    平台上传通道有 1MB 限制（nginx client_max_body_size），完整包 21MB 传不上去，
    所以索引不进包，改为运行时从内网 HTTP 源拉取。
    """
    qdir = BUNDLE / "runtime" / "qdrant"
    if not qdir.is_dir():
        print(f"✗ 索引目录不存在: {qdir}")
        sys.exit(1)

    out = RELEASE / "qdrant-index.tar.gz"
    if out.exists():
        out.unlink()
    log("打包索引…")
    with tarfile.open(out, "w:gz") as tar:
        for item in sorted(qdir.iterdir()):
            tar.add(item, arcname=item.name)
    log(f"索引包: {out}  ({out.stat().st_size/1024/1024:.1f} MB)")
    return out


def strip_index_from_bundle() -> None:
    """从 bundle 里剔除索引目录，让发布包能通过 1MB 限制。"""
    qdir = BUNDLE / "runtime" / "qdrant"
    if qdir.exists():
        shutil.rmtree(qdir)
        log("已从 bundle 剔除索引目录（改为运行时下载）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="强制重建索引")
    ap.add_argument("--no-pack", action="store_true", help="只准备 bundle 目录")
    ap.add_argument(
        "--slim",
        action="store_true",
        help="剔除索引，产出可过 1MB 上传限制的小包（索引改为运行时下载）",
    )
    args = ap.parse_args()

    check_prereq(args.rebuild)
    BUNDLE.mkdir(parents=True, exist_ok=True)

    build_index(force=args.rebuild)
    copy_code()
    write_env()

    if args.slim:
        make_index_archive()
        strip_index_from_bundle()

    log(f"bundle 总大小: {dir_size(BUNDLE)/1024/1024:.1f} MB")
    if not args.no_pack:
        pack()


if __name__ == "__main__":
    main()
