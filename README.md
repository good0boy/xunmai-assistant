# 寻脉问诊助手

以倪海厦经方思维整理的中医古籍与医案聊天助手。前后端分离，本地部署，知识库可重建。

> ⚠️ **定位说明**：本项目是**中医古籍与医案的学习参考工具**，不是在线诊疗。
> 不提供诊断、处方或个体化治疗建议。身体不适请找执业中医师面诊，急危重症请立即就医。

---

## 它是什么

参考 [jangviktor-web/nihaixia](https://github.com/jangviktor-web/nihaixia) 的知识库，把原本作为 Agent Skill 存在的 3.5M 字中医语料，做成一个能直接用的 Web 聊天应用。

和原项目的区别：

| | 原项目 | 本项目 |
|---|---|---|
| 形态 | Agent Skill（Markdown 知识库 + 提示词） | Web 应用（前端 + 后端 + 向量库） |
| 检索 | 依赖宿主工具 Grep/Read 翻文件 | 自建混合检索（稠密 + 稀疏 + LLM 重排 + 四级降级） |
| 模型 | 由宿主 Agent 决定 | 任意 OpenAI 兼容端点，写在 `.env` |
| 合规 | 单一免责声明 | 红旗症状拦截 + 三级免责 + 服务端确定性注入 |
| 图片 | 无 | 舌象/面色识别（需另配 VLM，未配则降级为文字引导） |

## 架构

```
┌──────────────┐   SSE    ┌─────────────────┐         ┌──────────┐
│  Vue 3 前端  │ ───────► │  FastAPI 后端    │ ──────► │  Qdrant  │
│  8080        │          │  8000           │  混合检索│  6333    │
└──────────────┘          │                 │         └──────────┘
                          │  ├ 四级降级检索  │
                          │  ├ 五层 prompt   │         ┌──────────┐
                          │  ├ 八查校验器    │ ──────► │  LLM API │
                          │  └ 免责注入      │         └──────────┘
                          └─────────────────┘
                                   │
                            ┌──────┴──────┐
                            │ SQLite 会话  │
                            └─────────────┘
```

检索链路：

```
用户问题
 ├ 实体抽取（词典最长匹配）→ 方名 / 症状 / 病名 / 穴位 / 条文号
 ├ 查询扩展（口语 → 中医术语）
 ├ 并行召回：稠密 top-40（bge-small-zh）+ 稀疏 top-40（jieba + BM25）
 ├ RRF 融合 → 实体加分 → 同文档去重
 ├ LLM 重排（0-3 分，阈值 ≥2）
 ├ 邻块扩展
 └ 四级降级：精确混合 → 同义词扩展 → 宽召回 → 声明未覆盖
```

## 快速开始

前置：Docker Desktop、约 2GB 磁盘（含 94MB 模型）。

```bash
# 1. 配置
cp .env.example .env
#    编辑 .env，至少填写 LLM_API_KEY

# 2. 拉取知识库（约 7.5MB）
mkdir -p backend/kb_src
git clone --depth 1 https://github.com/jangviktor-web/nihaixia.git backend/kb_src/nihaixia

# 3. 起服务
docker compose up -d --build

# 4. 建索引（首次必须，约 5-6 分钟）
docker compose run --rm backend python -m scripts.build_index

# 5. 打开
#    http://localhost:8080
```

模型下载走 HuggingFace，国内网络请确认 `.env` 里 `HF_ENDPOINT=https://hf-mirror.com` 已生效。

## 配置

所有配置在 `.env`。关键项：

| 变量 | 说明 | 默认 |
|---|---|---|
| `LLM_BASE_URL` | OpenAI 兼容端点 | `https://api.deepseek.com` |
| `LLM_API_KEY` | 主模型 Key | — |
| `LLM_MODEL` | 主模型名 | `deepseek-chat` |
| `LLM_TEMPERATURE` | 1.0–1.3 之间，影响口吻 | `1.1` |
| `VLM_ENABLED` | 是否启用舌象识别 | `false` |
| `VLM_MODEL` | 视觉模型（如 `qwen-vl-max`） | — |
| `RAG_RERANK_BACKEND` | `llm` / `onnx` / `none` | `llm` |
| `RAG_DEGRADATION_MAX_LEVEL` | 降级到第几级 | `3` |
| `APP_PASSCODE` | 单口令保护，留空不启用 | — |
| `HF_ENDPOINT` | 模型下载镜像 | — |

**关于视觉模型**：DeepSeek 官方 API 不支持图片输入。要用舌象识别，需另配一个支持视觉的 OpenAI 兼容端点（通义 `qwen-vl-max`、智谱 `glm-4v-plus` 均可）。不配也能用，图片入口会降级为「引导用户用文字描述舌象」。

## 目录

```
backend/
  app/
    api/v1/          接口层（chat SSE / conversations / kb 调试 / files）
    rag/
      chunking/      Markdown 结构切分 + 医案表格三级表示 + SimHash 去重
      extract/       实体抽取 + jieba 分词 + 稀疏向量
      index/         Qdrant 读写 + fastembed + 建库主流程
      retrieve/      混合召回 + RRF + LLM 重排 + 四级降级
      prompts/       五层 prompt 组装 + 八查校验器
    prompts/         纯文本 prompt 资产（改这个就能调口吻和格式）
    config/          词典与规则（方名/穴位/症状/红旗症状/同义词）
  scripts/           建库、评测、词典抽取、模型缓存修复
  kb_src/            知识库源（不入 git）
frontend/
  src/
    composables/     useSSE（手写流式解析）、useMarkdown
    components/      chat / markdown / citation / safety
    views/           ChatView、KnowledgeView
data/                索引、数据库、上传图、模型缓存（全部挂卷）
```

## 调口吻和格式

改 `backend/app/prompts/` 下的文件即可，不用动代码：

- `l0_system.md` —— 身份、安全优先级、硬性禁令
- `l1_style_card.md` —— 八条输出铁律、条文卡片模板、正反例（**最值得打磨的文件**）
- `l2_citation.md` —— 引用角标规则
- `disclaimer/*.md` —— 三级免责文案

改完重启后端生效。

输出格式由 `app/rag/prompts/validator.py` 做确定性校验。不通过会带着具体问题再请求一次修格式，仍不过就按原样输出并在 `done` 事件里返回 `issues`。

## 知识库构建

```bash
# 只切分不写库，看诊断报告
docker compose run --rm backend python -m scripts.build_index --dry-run
# 报告在 data/reports/dryrun.md

# 只重建单个文件
docker compose run --rm backend python -m scripts.build_index --only modules/04_jingui.md

# 重建词典（改了知识库之后）
docker compose run --rm backend python -m scripts.gen_dicts
```

## 知识库构建

当前规模：43 个文件 → 5281 块，去重丢弃 265 块。

医案表（1257 例 / 685KB）走三级表示：表结构卡 → 行级块 → 按病名/方名聚合卡。聚合卡解决「行级块丢失全局视角」的问题。

### embedding 模型随项目打包

`backend/kb_src/models/` 带着一份完整的 HuggingFace 缓存布局
（`BAAI/bge-small-zh-v1.5` 的 ONNX 优化版，90.4MB）。

**为什么**：模型 94MB 装不进发布包，而内网服务器可能没外网——拉不到模型就只剩
BM25 关键词检索，语义召回整个消失。随包走就不用依赖任何外部下载。

启动时 `app/main.py` 的 `_ensure_model_cache()` 会把它拷进可写缓存目录
（部署时 `/kb` 是只读挂载，fastembed 要建 `.locks` 需要可写）。
详见 `backend/kb_src/models/README.md`。

> 换模型 = 重建索引（向量维度与分布都变）。维度是实测探测的，不信模型声明。

### 三个独立集合

经典原文与倪师解读**分开建集**，各自可调权、可溯源：

| 集合 | 内容 | 块数 | 优先级 | 角色 |
|---|---|---|---|---|
| `tcm_kb` | 倪师解读（43 个 md） | 5281 | 1~2 | **主**，优先引用 |
| `tcm_classics` | 经典原文（21 本典籍 + 汉唐方 89 首） | ~5000 | 3 | 辅，补位；**与倪师组同源时让位** |
| `tcm_judges` | 倪师判语（19294 条字幕级引文） | ~4000 | 4 | **不当独立证据**，只给倪师命中挂「倪师原话 + B站出处」 |

「同源」用 `origin` 字段判定（典籍 slug，如 `shanghan_lun`）：同一典籍的倪师解读与经典原文
算同源，检索合并时倪师组优先，经典原文只占补位名额。

外部语料的来源、许可与用途记在 `backend/kb_src/external/_SOURCES.md`。

```bash
# 拉取外部语料（一次）
python -m scripts.fetch_external_kb

# 只补建某几组，不动其它组
python -m scripts.build_index --groups classics,judges
```

**判语库的处理**：OCR 有错字（「反与黄苓汤撤其热脉迟为赛」），但**出处信息本身可靠**。
所以判语只当出处索引（带 B 站视频号与时间戳，可回放核对），不当证据。
`scripts/build_judge_corpus.py` 会应用勘误账本（53 条）里已登记的错字修正。

## 评测

```bash
docker compose run --rm backend python -m scripts.eval_retrieval        # 检索 hit@k
docker compose run --rm backend python -m scripts.eval_answer --limit 60  # 答案质量
```

**检索评测**：65 条 gold query（方名直查 / 证型辨证 / 口语症状 / 医案 / 针灸 / 本草 / 经典原文），
报告在 `data/reports/eval_result.md`。

**答案质量评测**：从 TCMQA（33872 题）或 LingLan 专家基准分层抽样，走完整检索+生成链路，
判标准答案是否被回答覆盖（去标点子串匹配，退化为 2-gram 覆盖率 ≥0.6），
报告在 `data/reports/eval_answer_*.md`。

> 为什么要两种评测：35 条自建 gold set 对配置改动敏感到 ±6%（改一个 boost 值就掉 2~5 条），
> hit@1 差 14% 只等于 5 条样本 —— 根本不能当回归门禁。答案质量评测能抽几百题，
> 统计上稳得多。

## few-shot 示例

`app/prompts/few_shot.md` 存 3 条高质量示例，运行时按问句的 2-gram 重合度**动态挑 2 条**
插入对话（不是全量注入，20 条要 2000+ tokens）。

示例里的剂量与条文**全部取自知识库原文**（`references/distilled` 感冒六大经方速查），
不能凭记忆写 —— 示例里出现编造的剂量会直接教坏模型。

## 流式输出链路

对话是 SSE，三个环节必须都通才能看到逐字输出：

```
浏览器 fetch + ReadableStream   →   FastAPI StreamingResponse   →   LLM 网关
        frontend/src/composables/useSSE.ts        backend/app/api/v1/chat.py
```

后端在 `chat_service.stream_chat` 里用 `aiter_lines` 逐行读模型响应，`yield ("delta", {"text": ...})` 一路推到响应体；`_wrapped` 用队列 + 心跳（15s 一次 `: ping`）包一层，防止中间层掐断空闲连接。

**踩过的坑（值得记）**：流式一开始完全不生效——界面上一个字符都不出，要等生成结束刷新页面才看到答案。根因不在后端也不在代理，而在前端 store：

```ts
const target = () => messages.value.find((m) => m.id === localAsstId)  // 按 id 反查
case 'meta': if (m) m.id = data.assistant_message_id                   // ← 改写了 id
```

`meta` 事件在 LLM 调用之前到达，把消息 id 换成了服务端 id。此后 `target()` 再也定位不到这条消息，**所有 delta / citation / done 事件全被丢弃**，而 `MessageList` 的 `:key="m.id"` 还会让组件重建。修法是 id 永不改动（服务端 id 存 `server_id`），定位改用稳定引用兜底。

**教训**：之前只验证了 SSE 协议层（Python 直接读事件流），没验证前端渲染层，所以这个 bug 一直没暴露。现在两层都有测试：

| 层 | 位置 | 覆盖 |
|---|---|---|
| 事件解析 | `frontend/src/composables/useSSE.test.ts` | 跨包拼接、心跳忽略、多事件同包、HTTP 错误 |
| 状态消费 | `frontend/src/stores/chat.test.ts` | meta 不改 id、delta 实时落文、done 收敛、引用免责 |
| 后端时序 | `backend/scripts/_timing_e2e.py` | 首字节延迟、delta 到达速率分布 |
| 整包验收 | `backend/scripts/_release_verify.py` | 从 tar.gz 解压启动 → 索引就绪 → 检索 → SSE 渐进 |

`useSSE.test.ts` 里特意覆盖了「一个事件被拆到多个网络包」——服务端一次 `yield` 不保证对应一个网络分片，解析必须靠缓冲拼接。

## 测试

```bash
cd backend && python -m pytest -q        # 后端 58 项
cd frontend && npm test                  # 前端 12 项
cd frontend && npm run typecheck         # vue-tsc
```

前端测试走 `npm test`（`scripts/run-tests.mjs` 包装），**不要直接 `npx vitest run`**：本机在受限环境（WorkBuddy 沙箱只放行部分写路径）下，vitest transform 模块时写 SSR 缓存会被判越权，报 EPERM 并中断整个 run——表现为「只跑了一半用例」并给出**假通过**。

包装器做了三件事：显式扫描全部测试文件逐个传给 vitest（不依赖其自动发现）、临时目录指到工作区内、**在确认是 EPERM 时自动重试**（沙箱对这次写入的判定是间歇性的）。真实断言失败不会被重试掩盖。

## 单容器发布

三容器架构不适合直接丢给只能跑单进程的发布平台。为此提供一个打包脚本，把系统压成一个进程：

```bash
cd frontend && npm run build && cd ..
cd backend && python -m scripts.build_release
# 产物：dist_release/xunmai-release.tar.gz
```

打出来的包长这样：

```
main.py            入口（python main.py 或 uvicorn main:app）
pyproject.toml     让发布平台识别为 Python 项目
requirements.txt
.env               运行配置
app/               后端代码 + prompts + 领域词典
web/               前端构建产物
runtime/
  qdrant/          预构建索引（Qdrant 本地模式，约 12MB）
  app.db           含 kb_meta 的 SQLite
```

关键取舍：

- **Qdrant 从服务改成进程内嵌**（`QDRANT_MODE=local`）。本地模式实测支持 dense + sparse + IDF + RRF 融合 + 持久化，唯一限制是 payload 索引不生效（本地是暴力检索，5k 量级无影响）。
- **索引预构建后打包**，容器启动不需要再花 5 分钟建库。
- **embedding 模型不打进包**（94MB，超过多数平台的上限），改为首次启动时从 `HF_ENDPOINT` 下载到 `runtime/models`。**集群必须能访问外网**，否则要改成镜像内置。
- **`.env` 里含 LLM key**。发布包会带上当前的 key，注意分发范围；key 换了必须重新打包发布。

单容器模式由后端直接托管前端，`FRONTEND_DIST=./web` 生效时会自动开启 SPA 路由回退。原 docker compose 部署不受影响（默认 `QDRANT_MODE=server`）。

### 线上环境（publish-mcp 私有云 K8s）

| 项 | 值 |
|---|---|
| 应用显示名 | `华佗在世_gdh` |
| 共享链接 | http://gdh-27ff.apps.172.30.0.144.nip.io |
| namespace | `app-gdh-27ff` |
| 资源申请 | cpu=8，memory=16Gi |
| 镜像 | `172.30.0.141:89/phoenix-apps/gdh-27ff:7b53701ca709` |

**改名的副作用必须知道**：平台用 app_name 派生 namespace 和域名，非 ASCII 字符会被剥离——`华佗在世_gdh` 实际落地成 `gdh`，域名也跟着变成 `gdh-*.apps...`。中文只作为列表里的显示名，不会出现在 URL 上。应用名重复时平台自动加随机后缀（`gdh-27ff`），所以每次换 source 重新部署，域名都会变。

### 发布通道的三个坑（都踩过）

1. **上传体积限制**。平台文档写 36MB，但 nginx 的 `client_max_body_size` 实际是 1MB，21MB 的包直接 413。现在已放开。
2. **安全档拒绝 git 源和远程 URL**。平台在 block 档下无法核验内容，会直接拒绝发布，要求走 `upload_package`。`upload_package` 走 MCP 协议传 base64，21MB 包会膨胀到 29MB，仍受 nginx 限制。**最省事的上传方式是直接 `POST /upload` 传原始 tar.gz**（不走 base64，少 33% 体积）：

   ```bash
   curl -X POST http://publish.chinagasholdings.com/upload \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/gzip" \
     --data-binary @xunmai-release.tar.gz
   # => {"ok":true,"source_url":"uploads/<hash>.tar.gz"}
   ```

   然后 `deploy(source="uploads/<hash>.tar.gz")`。
3. **构建期依赖下载慢会导致构建失败**。集群出网到 pypi.org 很慢，pip 装依赖跑满 10 分钟就被平台判超时，报 `BuildError` 且不归因。`requirements.txt` 已改为**钉死版本 + 阿里云源优先**（`--index-url https://mirrors.aliyun.com/pypi/simple`），构建时间从 10 分钟以上降到 1 分钟内。

另外两点：

- **平台按 source 去重**。换 source 会被当成全新应用，创建新 namespace、分配新域名，旧应用得单独下线（`unpublish` 只认原始 source，不认链接和应用名）。
- **`.env` 打在镜像里**，平台不支持注入环境变量。换 key 必须重新打包。

### 索引与模型的分发

发布包里有预构建的 Qdrant 索引（20.7MB），但**没有 embedding 模型**（ONNX 94MB，gzip 后仍有 105MB，物理上塞不进包）。

因此 `scripts/build_release.py` 支持 `--slim`：剔除索引，产出 0.5MB 的小包，容器启动时按 `KB_INDEX_SOURCES`（逗号或竖线分隔的多源）依次尝试下载索引，失败则降级运行。完整包则索引直接内置，不走下载。

**线上当前状态**：`kb_status=ready`（5281 点）但 `dense_available=false`——集群拉不到 embedding 模型，检索退化为「BM25 + LLM 重排」，稀疏通道照常工作，语义召回缺失。

## 已知限制

- **模型未配置时无法对话**，`/health` 会返回 `llm_configured: false`。
- **Windows 下 Qdrant 用 bind mount 有权限风险**。删集合会触发 `Permission denied`，所以重建走「清空点」而非「删集合」。若确实需要删集合，先 `docker compose restart qdrant`。
- **医案原表标签有错标**（如 #193 红斑狼疮被标为肺癌）。已做正文反推 + 人工覆盖表，但无法全量修复。检索层因此只做 soft boost，绝不做硬过滤。
- **条文号是倪师讲课编号**，非宋本编号，展示时会标注。
- 语料源自倪海厦讲义。**个人学习使用无碍，公开分发需自行评估版权**。

## 合规红线

以下三件事是服务端强制执行的，不依赖模型配合：

1. **红旗症状**（`app/config/red_flags.yaml`）：胸痛大汗、中风征象、呼吸困难、大出血等，命中即在正文最前插入就医提示，且禁止输出剂量。
2. **免责声明**：每条回答都注入，不由模型生成，且已从原 skill 的「文末一次」改为「每条都有」。
3. **路径泄漏清理**：剥掉模型输出里可能夹带的文件路径、行号。

原始 skill 的「禁止正文出现建议就医话术」在 Web 场景下已改为条件化，原因是功能上线面向真人，安全优先于风格纯度。
