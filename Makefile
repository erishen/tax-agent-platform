.PHONY: install dev run run-web run-all test lint clean stop

# ---- Configuration ----
PYTHON ?= python3
UV ?= uv

# ---- Install ----
install:
	$(UV) sync --group dev

install-web:
	cd web && npm install

install-all: install install-web

# ---- Run ----
run:
	@echo ">> 释放 8001 端口(如有占用)..."
	@-PIDS=$$(lsof -ti:8001); if [ -n "$$PIDS" ]; then echo "killing: $$PIDS"; kill $$PIDS; sleep 1; fi
	@echo ">> 启动 tax-agent-platform (uvicorn --reload :8001)"
	# `exec` 让 uvicorn 直接顶替 make 派生的 shell 成为前台进程，
	# Ctrl+C 的 SIGINT 就能直接命中 uvicorn（含 --reload 的 worker 子进程），
	# 否则 make 默认不把信号转发给子进程，uvicorn worker 会变成杀不掉的孤儿。
	exec $(UV) run uvicorn tax_agent_platform.app:app --reload --host 0.0.0.0 --port 8001

# 兜底：make run 之外想手动停掉 8001 上的实例（含杀不掉的孤儿）。
stop:
	@-PIDS=$$(lsof -ti:8001); if [ -n "$$PIDS" ]; then echo "stopping: $$PIDS"; kill $$PIDS; else echo "8001 上无监听进程"; fi

run-web:
	cd web && npx vite --port 5173

run-all: run run-web

# ---- Test ----
test:
	$(UV) run python -m pytest -v

test-cov:
	$(UV) run python -m pytest -v --tb=short -q

# ---- Lint ----
lint:
	$(UV) run ruff check .

lint-fix:
	$(UV) run ruff check --fix .

format:
	$(UV) run ruff format .

# ---- Clean ----
clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} +

# ---- Quick check ----
check: lint test
