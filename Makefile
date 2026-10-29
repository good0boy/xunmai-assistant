SHELL := /bin/bash
PY := backend/.venv/Scripts/python.exe
ifeq ($(OS),)
	PY := backend/.venv/bin/python
endif

.PHONY: help up down logs restart kb kb-dry eval test lint front-test front-build back-dev clean

help:
	@echo "寻脉问诊助手 —— 常用命令"
	@echo ""
	@echo "  make up        启动全部服务（qdrant + backend + frontend）"
	@echo "  make down      停止服务"
	@echo "  make logs      查看日志"
	@echo "  make kb        构建知识库索引（首次必须执行）"
	@echo "  make kb-dry    只切分不写库，输出诊断报告"
	@echo "  make eval      检索评测"
	@echo "  make test      跑后端单元测试"
	@echo "  make front-test 跑前端单元测试"
	@echo "  make lint      代码静态检查（ruff）"
	@echo "  make front-dev 前端开发服务器（含热更新）"
	@echo "  make back-dev  后端开发服务器"
	@echo ""

up:
	docker compose up -d --build
	@echo ""
	@echo "前端 http://localhost:8080"
	@echo "接口 http://localhost:8000/api/docs"

down:
	docker compose down

logs:
	docker compose logs -f --tail=80

restart:
	docker compose restart

# 首次使用必须先跑这个，约 9 分钟（三集合共 1.2 万块）
kb:
	docker compose run --rm backend python -m scripts.build_index

kb-dry:
	docker compose run --rm backend python -m scripts.build_index --dry-run

# 只补建某几组，不动其它组：make kb-classics / kb-judges
kb-classics:
	docker compose run --rm backend python -m scripts.build_index --groups classics

kb-judges:
	docker compose run --rm backend python -m scripts.build_index --groups judges

eval:
	docker compose run --rm backend python -m scripts.eval_retrieval

# 答案质量评测（会调 LLM，每题约 25 秒；60 题约 25 分钟）
eval-answer:
	docker compose run --rm backend python -m scripts.eval_answer --limit 60 --source tcmqa

eval-answer-linglan:
	docker compose run --rm backend python -m scripts.eval_answer --limit 60 --source linglan

# 拉取外部语料（经典原文 / 判语 / 评测题库）
fetch-kb:
	docker compose run --rm backend python -m scripts.fetch_external_kb

# 从 entries.jsonl 重新生成判语语料（含勘误修正）
build-judges:
	docker compose run --rm backend python -m scripts.build_judge_corpus

# 镜像里不装测试依赖（生产镜像保持精简），跑测试时临时装
test:
	docker compose run --rm backend sh -c "pip install -q -e '.[dev]' && python -m pytest -q"

lint:
	docker compose run --rm backend sh -c "pip install -q ruff && ruff check app scripts tests"

front-test:
	cd frontend && npm test

front-build:
	cd frontend && npm run build

# 本地开发（不用容器）
front-dev:
	cd frontend && npm run dev

back-dev:
	cd backend && $(PY) -m uvicorn app.main:app --reload --port 8000

gen-dicts:
	cd backend && $(PY) -m scripts.gen_dicts

clean:
	rm -rf data/qdrant data/app.db data/reports/*.md
	@echo "已清理本地索引与数据库（模型缓存保留）"
