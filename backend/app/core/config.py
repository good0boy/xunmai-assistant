"""应用配置。所有可调项来自环境变量 / .env。"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.paths import PROJECT_ROOT

# .env 必须按代码位置找，不能按 cwd —— 发布平台启动容器的 cwd 不一定是包根目录，
# 读不到 .env 就会静默退回默认值（例如 /data/app.db），进而启动失败。
_ENV_FILES = (str(PROJECT_ROOT / ".env"), str(PROJECT_ROOT.parent / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 应用 ----------
    app_env: Literal["dev", "prod"] = "dev"
    app_port: int = 8080
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    app_passcode: str = ""
    admin_token: str = ""
    rate_limit_per_min: int = 20
    daily_token_budget: int = 2_000_000

    # ---------- 主对话模型 ----------
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 1.1
    llm_max_tokens: int = 2500
    llm_timeout_s: float = 180.0

    # ---------- 工具型调用 ----------
    utility_model: str = "deepseek-chat"
    utility_temperature: float = 0.0
    utility_max_tokens: int = 800

    # ---------- 视觉模型（可选） ----------
    vlm_enabled: bool = False
    vlm_base_url: str = ""
    vlm_api_key: str = ""
    vlm_model: str = ""
    vlm_timeout_s: float = 60.0

    # ---------- Embedding ----------
    embed_model: str = "BAAI/bge-small-zh-v1.5"
    # 随项目打包的 embedding 模型目录（HuggingFace 缓存布局）。
    # 部署时 /kb 是只读挂载，而 fastembed 需要可写目录（要建 .locks），
    # 所以启动时把这里的模型拷到 fastembed_cache_path。
    kb_models_dir: str = "kb_src/models"
    # 实际维度由 embedder 探测后写回，此处仅作兜底默认（实测 fastembed 0.9 映射版为 512）
    embed_dim: int = 512
    embed_batch: int = 64
    embed_threads: int = 8
    embed_query_prefix: str = "为这个句子生成表示以用于检索相关文章："
    # 本地开发默认指向随包模型（kb_src/models）；容器里由 compose 显式覆盖为
    # /data/models（可写卷）。没有 .env 时 Windows 本地 /data/models 是个
    # 莫名其妙的路径 —— 默认值必须对「零配置启动」友好。
    fastembed_cache_path: str = "kb_src/models"
    # fastembed/huggingface_hub 只认环境变量，不读 .env，需要在启动时注入
    hf_endpoint: str = "https://hf-mirror.com"

    # ---------- 向量库 ----------
    # server：连 Qdrant 服务（docker compose 默认）
    # local ：进程内嵌 Qdrant，读本地目录（单容器发布用）
    qdrant_mode: Literal["server", "local"] = "server"
    qdrant_path: str = "./runtime/qdrant"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "tcm_kb"
    # 知识库分组，各自独立建集（可分别调权、分别溯源、分别维护）：
    #   tcm_kb       倪师解读（主，优先引用）
    #   tcm_classics 经典原文（辅，倪师组未覆盖时补位）
    #   tcm_judges   判语（最低优先级，只用来补充「倪师原话 + 视频出处」）
    qdrant_collection_classics: str = "tcm_classics"
    qdrant_collection_judges: str = "tcm_judges"
    # 同源时（同一典籍的不同呈现）倪师解读优先，经典原文只占补位名额
    kb_classics_weight: float = 0.75
    kb_judges_weight: float = 0.5

    # ---------- 前端托管（单容器模式下由后端直接服 dist） ----------
    frontend_dist: str = ""

    # ---------- 检索 ----------
    rag_top_k_dense: int = 40
    rag_top_k_sparse: int = 40
    rag_rrf_k: int = 60
    rag_rerank_backend: Literal["llm", "onnx", "none"] = "llm"
    rag_rerank_top_n: int = 12
    rag_rerank_min_score: int = 2
    rag_final_k: int = 5
    rag_max_context_chars: int = 4500
    rag_query_rewrite: Literal["llm", "rules", "off"] = "rules"
    rag_degradation_max_level: int = 3
    rag_max_chunks_per_doc: int = 3
    rag_vector_weight: float = 1.0
    rag_sparse_weight: float = 1.0
    # 实体加分是**绝对分**，且刻意大于 RRF 融合分的量级（0.01~0.1），
    # 实现「命中方名即置顶」。这是业务语义不是量纲错误，改动前先看
    # hybrid.py 里记的实测对比（改成乘性/归一化/削弱症状加分都会让 hit@5 掉 6~11 个点）。
    rag_formula_boost: float = 0.15
    rag_disease_boost: float = 0.10
    rag_symptom_boost: float = 0.06

    # ---------- 知识库构建 ----------
    kb_root: str = "/kb"
    kb_target_chunk_chars: int = 700
    kb_max_chunk_chars: int = 1200
    kb_min_chunk_chars: int = 120
    kb_overlap_chars: int = 80
    kb_embed_context_prefix: bool = True
    # 外部知识库（经典原文 / 判语）目录，与主语料 backend/kb_src/nihaixia 同级
    kb_external_root: str = "kb_src/external"
    # 经典原文与判语的检索优先级：数字越小越权威。
    # 倪师解读主语料 priority 为 1~2；经典原文同样给 2 —— 它是权威典籍，
    # 压到 3 会让 L1 精确检索直接把它过滤掉，问「千金要方怎么用」根本召不回。
    # 同源时的先后顺序由「倪师优先 + 同源让位」机制保证，不靠 priority 压制。
    # 判语 4：只用于补充出处，不作为主要证据。
    kb_classics_priority: int = 2
    kb_judges_priority: int = 4
    # 候选池里倪师解读的保底名额占比。倪师解读是本项目核心资产（表达风格、
    # 辨证抓手都在那），经典原文最多占剩下的部分。倪师组命中不足时
    # 经典原文可以吃满剩余。
    rag_nihai_ratio: float = 0.65
    # 经典原文的分数折扣。同源时靠 origin 让位，但非同源时（比如倪师的
    # 厥阴病讲解 vs 针灸甲乙经里的厥阴）两者内容相近会互相竞争，
    # 折扣让经典原文在分数接近时稳定排在倪师解读之后。
    # ⚠ 0.85 这个值是在「dense 向量全零」的坏基线上扫出来的，
    # 已在 2026-10-28 用修好的索引重扫，改动前先看 data/reports/eval_result.md。
    rag_classics_score_scale: float = 0.85

    # ---------- 数据库 / 存储 ----------
    db_url: str = "sqlite+aiosqlite:////data/app.db"
    upload_dir: str = "/data/uploads"
    max_upload_mb: int = 8
    image_max_edge: int = 1600
    image_retention_days: int = 90

    # ---------- 会话上下文 ----------
    # 历史注入按 token 预算从最新往回装（_trim_history），条数只是硬上限。
    # 10 轮 = 20 条消息；预算 2000 token 在超长会话时先丢最旧的内容。
    history_max_turns: int = 10
    history_token_budget: int = 2000
    summary_trigger_tokens: int = 2500
    evidence_token_budget: int = 4500

    # ---------- 安全与合规 ----------
    disclaimer_mode: Literal["standard", "strict"] = "standard"
    red_flag_enabled: bool = True

    # ---------- 派生 ----------
    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    @property
    def vision_enabled(self) -> bool:
        return bool(self.vlm_enabled and self.vlm_base_url and self.vlm_api_key and self.vlm_model)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key)

    @field_validator("app_passcode", "admin_token", mode="before")
    @classmethod
    def _strip_secret(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
