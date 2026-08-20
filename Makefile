# open-cam 常用开发命令
# 用法：make help
#
# 生命周期：make start / stop / restart；改完代码看 make next。
# 默认 start 会 --reload opencam/（保存 .py 自动换进程）。关热加载：RELOAD=0 make start。

UV ?= uv
PORT ?= 8600
RELOAD ?= 1

ifeq ($(RELOAD),1)
RELOAD_FLAGS := --reload --reload-dir opencam \
	--reload-exclude models.py \
	--reload-exclude 'migrations/*'
else
RELOAD_FLAGS :=
endif

.DEFAULT_GOAL := help

.PHONY: help install install-dev start start-mock stop restart restart-mock \
	test openapi config clean revision ui ui-build next

help: ## 显示可用命令
	@echo "怎么启动 / 重启："
	@echo "  make start              后端 8600（默认热加载 opencam/*.py）"
	@echo "  make start-mock         同上，不下载 YOLO"
	@echo "  make stop / restart     释放或重启 8600"
	@echo "  make ui                 前端 next dev 5173（必须另开 start；浏览器开 5173，左下角 N 是 Next Overlay）"
	@echo "  make ui-build           编译 web/out；随后只 start 即可在 8600 打开控制台"
	@echo "改了什么 → 做什么：make next"
	@echo "  后端 .py     start 的 reload 会换进程（不含 models.py / migrations）；没 reload 则 make restart"
	@echo "  models.py    make revision m=\"说明\" → review 脚本 → 控制台横幅确认重启（或 make restart）"
	@echo "  web/src      有 make ui 则已热更新；只有 start 则 make ui-build"
	@echo "  API 路由     make restart（或等 reload）后 make openapi"
	@echo "  端口被占     make stop 后再 start，不要再开一个进程"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## 创建虚拟环境并安装项目（需要 uv）
	$(UV) venv --python 3.12
	$(UV) pip install -e .

install-dev: install ## 安装项目 + 开发依赖（pytest）
	$(UV) pip install -e . --group dev

start: ## 启动后端（8600）。默认 --reload；无模型用 start-mock；改前端另开 make ui
	@if lsof -nP -iTCP:$(PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "端口 $(PORT) 已被占用。先 make stop，或 make restart。"; \
		exit 1; \
	fi
	PORT=$(PORT) OPENCAM_RELOAD=$(RELOAD) $(UV) run uvicorn opencam.main:app --port $(PORT) $(RELOAD_FLAGS)

start-mock: ## 同 start，但 mock detector（不下载 yolov8n.pt）
	OPENCAM_DETECTOR=mock $(MAKE) start PORT=$(PORT) RELOAD=$(RELOAD)

stop: ## 停掉占用 PORT 的进程（默认 8600）
	@pids=$$(lsof -nP -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null); \
	if [ -z "$$pids" ]; then echo "端口 $(PORT) 没有在听的进程"; \
	else echo "停止 $$pids"; kill $$pids; fi

restart: stop ## 先 stop 再 start
	@sleep 0.4
	@$(MAKE) start PORT=$(PORT) RELOAD=$(RELOAD)

restart-mock: stop ## 先 stop 再 start-mock
	@sleep 0.4
	@$(MAKE) start-mock PORT=$(PORT) RELOAD=$(RELOAD)

next: ## 根据 git 改动打印必做项（重启 / revision / ui-build / openapi）
	$(UV) run python scripts/dev_next.py

test: ## 运行全部测试（强制 mock detector，不下载模型）
	OPENCAM_DETECTOR=mock $(UV) run pytest

openapi: ## 重新导出 docs/openapi.json（改动 API 后必跑）
	$(UV) run python scripts/export_openapi.py

revision: ## 生成数据库迁移脚本（用法：make revision m="加 xx 列"，生成后人工 review）
	$(UV) run alembic revision --autogenerate -m "$(m)"

config: ## 生成 config.yaml（已存在则不覆盖）
	@test -f config.yaml && echo "config.yaml 已存在，跳过" || cp config.example.yaml config.yaml

ui: ## 只起 next dev（5173），必须另开 make start；浏览器开 5173
	cd web && npm run dev

ui-build: ## 编译 web/out 给 FastAPI 挂载。test_web 前置；无热更新
	cd web && npm ci && npm run build

clean: ## 清理缓存与临时文件（不动 data/ 运行时数据）
	rm -rf .pytest_cache
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
