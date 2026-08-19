# open-cam 常用开发命令
# 用法：make help

UV ?= uv
PORT ?= 8600

.DEFAULT_GOAL := help

.PHONY: help install install-dev run run-mock test openapi config clean help

help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## 创建虚拟环境并安装项目（需要 uv）
	$(UV) venv --python 3.12
	$(UV) pip install -e .

install-dev: install ## 安装项目 + 开发依赖（pytest）
	$(UV) pip install -e . --group dev

run: ## 启动服务（默认端口 8600，PORT=xxxx 覆盖）
	$(UV) run uvicorn opencam.main:app --port $(PORT)

run-mock: ## 以 mock detector 启动服务（不下载 yolov8n.pt）
	OPENCAM_DETECTOR=mock $(UV) run uvicorn opencam.main:app --port $(PORT)

test: ## 运行全部测试（强制 mock detector，不下载模型）
	OPENCAM_DETECTOR=mock $(UV) run pytest

openapi: ## 重新导出 docs/openapi.json（改动 API 后必跑）
	$(UV) run python scripts/export_openapi.py

config: ## 生成 config.yaml（已存在则不覆盖）
	@test -f config.yaml && echo "config.yaml 已存在，跳过" || cp config.example.yaml config.yaml

clean: ## 清理缓存与临时文件（不动 data/ 运行时数据）
	rm -rf .pytest_cache
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
