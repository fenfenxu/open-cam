# open-cam 常用开发命令
# 用法：make help
#
# 生命周期：make start / stop / restart；start 会启动完整开发环境。
# 只启动后端：make backend。关后端热加载：RELOAD=0 make start。

UV ?= uv
PORT ?= 8600
UI_PORT ?= 5173
RELOAD ?= 1

ifeq ($(RELOAD),1)
RELOAD_FLAGS := --reload --reload-dir opencam \
	--reload-exclude models.py \
	--reload-exclude 'migrations/*'
else
RELOAD_FLAGS :=
endif

.DEFAULT_GOAL := help

.PHONY: help install install-dev start start-mock backend \
	serve stop restart restart-mock test openapi config clean revision ui ui-build \
	dev-status

help: ## 显示可用命令
	@echo "怎么启动 / 重启："
	@echo "  make start              完整开发环境：后端 8600 + Next 前端 5173"
	@echo "  make start-mock         同上，但使用 mock detector，不下载 YOLO"
	@echo "  make backend            只启动 FastAPI 后端 8600（高级/调试用）"
	@echo "  make stop / restart     停止或重启完整开发环境"
	@echo "  make serve              构建前端并以单端口 8600 运行"
	@echo "开发辅助：不确定改动如何生效时运行 make dev-status"
	@echo "  后端 .py     自动 reload（不含 models.py / migrations）"
	@echo "  models.py    make revision m=\"说明\" → review 脚本 → 控制台横幅确认重启（或 make restart）"
	@echo "  web/src      make start 已启动 HMR；单端口运行则 make serve"
	@echo "  API 路由     make restart（或等 reload）后 make openapi"
	@echo "  端口被占     make stop 后再 start，不要再开多个进程"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## 创建虚拟环境并安装项目（需要 uv）
	$(UV) venv --python 3.12
	$(UV) pip install -e .

install-dev: install ## 安装项目 + 开发依赖（pytest）
	$(UV) pip install -e . --group dev

start: ## 启动完整开发环境（FastAPI 8600 + Next.js 5173）
	PORT=$(PORT) UI_PORT=$(UI_PORT) RELOAD=$(RELOAD) OPENCAM_RELOAD=$(RELOAD) $(UV) run python scripts/dev_start.py

start-mock: ## 启动完整开发环境，但使用 mock detector
	OPENCAM_DETECTOR=mock $(MAKE) start PORT=$(PORT) RELOAD=$(RELOAD)

backend: ## 只启动 FastAPI 后端（高级/调试用）
	@if lsof -nP -iTCP:$(PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "端口 $(PORT) 已被占用。先 make stop，或 make restart。"; \
		exit 1; \
	fi
	PORT=$(PORT) OPENCAM_RELOAD=$(RELOAD) $(UV) run uvicorn opencam.main:app --port $(PORT) $(RELOAD_FLAGS)

stop: ## 停掉后端和前端开发进程
	@pids=$$(lsof -nP -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null; lsof -nP -tiTCP:$(UI_PORT) -sTCP:LISTEN 2>/dev/null | sort -u); \
	if [ -z "$$pids" ]; then echo "端口 $(PORT)/$(UI_PORT) 没有在听的进程"; \
	else echo "停止 $$pids"; kill $$pids; fi

restart: stop ## 先 stop 再启动完整开发环境
	@sleep 0.4
	@$(MAKE) start PORT=$(PORT) RELOAD=$(RELOAD)

restart-mock: stop ## 先 stop 再启动 mock 完整开发环境
	@sleep 0.4
	@$(MAKE) start-mock PORT=$(PORT) RELOAD=$(RELOAD)

dev-status: ## 不确定改动如何生效时，检查工作区并给出建议
	$(UV) run python scripts/dev_status.py

test: ## 运行全部测试（强制 mock detector，不下载模型）
	OPENCAM_DETECTOR=mock $(UV) run pytest

openapi: ## 重新导出 docs/openapi.json（改动 API 后必跑）
	$(UV) run python scripts/export_openapi.py

revision: ## 生成数据库迁移脚本（用法：make revision m="加 xx 列"，生成后人工 review）
	$(UV) run alembic revision --autogenerate -m "$(m)"

config: ## 生成 config.yaml（已存在则不覆盖）
	@test -f config.yaml && echo "config.yaml 已存在，跳过" || cp config.example.yaml config.yaml

ui:
	cd web && npm run dev

ui-build:
	cd web && npm ci && npm run build

serve: ## 构建前端后以单端口 8600 运行（无 HMR）
	@$(MAKE) ui-build
	@$(MAKE) backend PORT=$(PORT) RELOAD=0

clean: ## 清理缓存与临时文件（不动 data/ 运行时数据）
	rm -rf .pytest_cache
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
