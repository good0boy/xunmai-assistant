"""pytest 全局配置。

**必须在任何 app 模块导入前把 DB_URL 指到测试专用临时库**。

2026-10-29 事故：test_conversation_order.py 直接调 init_db()，写的是
生产库 data/app.db（.env 的 DB_URL 解析结果），把 5 个标题为「t」的
测试会话写进了主人的前端会话列表。测试永远不允许碰生产数据。

pydantic-settings 的优先级：os.environ > .env 文件 —— 这里设环境变量
即可压过 .env。conftest 在所有测试模块收集前加载，早于测试文件里的
`from app.db.session import ...`，时机正确。
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_TEST_DIR = Path(tempfile.mkdtemp(prefix="xunmai-test-"))
os.environ["DB_URL"] = f"sqlite+aiosqlite:///{_TEST_DIR.as_posix()}/app.db"

atexit.register(shutil.rmtree, _TEST_DIR, ignore_errors=True)
